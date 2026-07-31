import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { Card, Tag, Button, Input, Select, Space, Modal, Drawer, message, Tabs, Switch, Popover, Tooltip, Spin, Empty, Table, Alert } from 'antd'
import {
  ArrowLeftOutlined, PlayCircleOutlined, SaveOutlined,
  PlusOutlined, DeleteOutlined, HolderOutlined,
  ThunderboltOutlined, TagOutlined, AppstoreOutlined, ApiOutlined,
  FlagOutlined, WarningOutlined, CodeOutlined, CopyOutlined, FileTextOutlined,
  DesktopOutlined, CheckCircleOutlined, StarOutlined, StarFilled, ImportOutlined,
  DatabaseOutlined, CaretRightOutlined,
} from '@ant-design/icons'
import { api, getValidToken } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'
import { useEnv, buildEnvOptions } from '../../utils/env'
import ScriptEditor from '../../components/ScriptEditor'
import ScenarioVariables from '../../components/ScenarioVariables'
import ApiStepList, { generateApiCodeFromSteps } from '../../components/ApiStepList'
import { scenarioToNodes, nodeToStepPatch } from './apiStepAdapter'
import RunResultPanel from '../api-test/components/RunResultPanel'

const priorityColors = { P0: '#fff', P1: '#fff', P2: '#fff', P3: '#fff' }
const priorityBg = { P0: '#e8453c', P1: '#ff7d00', P2: '#4e8af0', P3: 'rgba(0,0,0,0.08)' }
const statusColors = { automated: '#0ea5a0', pending: '#faad14', removed: '#e8453c' }
const statusBg = { automated: '#e0f7f6', pending: '#fffbe6', removed: '#fff2f0' }
const statusLabels = { automated: '已自动化', pending: '待自动化', removed: '脚本已移除' }
const dotColors = { P0: '#e8453c', P1: '#ff7d00', P2: '#4e8af0', P3: 'rgba(0,0,0,0.15)', automated: '#0ea5a0', pending: '#faad14', removed: '#e8453c' }
const phaseColor = { setup: '#7c5cbf', action: '#0ea5a0', verify: '#0ea5a0' }
const phaseLabel = { setup: '准备', action: '操作', verify: '验证' }

// 接口视图详情：JSON 串美化展示，非 JSON 原样返回
function prettyJson(s) {
  if (s == null) return ''
  const str = String(s)
  try { return JSON.stringify(JSON.parse(str), null, 2) } catch { return str }
}

// 智能识别：从一堆抓包里挑出「编排一个接口测试场景」真正需要的接口。
// 规则(已用真实 stoa 抓包验证)：
//  1) 写操作(POST/PUT/DELETE/PATCH)= 场景核心操作，必选；
//  2) 噪音 GET(后台轮询/页面初始化/列表分页刷新)= 必不选；
//  3) 依赖回溯：某个 GET 若与「同一来源页、且时间在其之前」的写操作共享同一个 UUID
//     (该 GET 的响应里出现了写操作 URL/请求体里用到的 ID)→ 判为该写操作的数据依赖，选。
// 返回 { indices: 推荐勾选的下标, reasons: 每条的 {tag,pick} 说明 }。
const _API_NOISE = [
  /\/auth\/sso\/status/, /\/analytics\/health/, /\/notifications\//,
  /\/todos\/stats/, /\/auth\/me(\b|$|\?)/, /\/favorites\/check/,
  /\/sync-status/, /\/oplogs/, /\/versions(\b|$|\?)/, /\/unread-count/,
]
const _UUID_RE = /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi
const _isWrite = m => ['POST', 'PUT', 'DELETE', 'PATCH'].includes(String(m || '').toUpperCase())
function analyzeApiRequests(requests) {
  const list = Array.isArray(requests) ? requests : []
  const pathOf = r => String(r.path || r.url || '').replace(/^https?:\/\/[^/]+/, '')
  const refOf = r => { const h = r.requestHeaders || {}; return h.Referer || h.referer || '' }
  const timeOf = r => { const t = Date.parse(r.startedDateTime || r.starteddatetime || ''); return Number.isNaN(t) ? 0 : t }
  const idsIn = s => { const m = String(s || '').match(_UUID_RE) || []; return new Set(m.map(x => x.toLowerCase())) }

  const reasons = list.map(() => ({ tag: 'other', pick: false }))

  // 1) 写操作 / 噪音
  list.forEach((r, i) => {
    if (_isWrite(r.method)) { reasons[i] = { tag: 'write', pick: true }; return }
    const p = pathOf(r)
    if (_API_NOISE.some(re => re.test(p))) { reasons[i] = { tag: 'noise', pick: false }; return }
    // 纯分页列表 GET(?page= / ?page_size=)且路径不指向具体资源 → 列表刷新噪音
    if (/[?&]page(_size)?=/.test(p) && !/\/[0-9a-f-]{20,}(\/|$|\?)/.test(p)) { reasons[i] = { tag: 'noise', pick: false }; return }
  })

  // 2) 依赖回溯
  list.forEach((r, i) => {
    if (reasons[i].pick || reasons[i].tag === 'noise' || _isWrite(r.method)) return
    const respIds = idsIn(r.responseBody)
    if (!respIds.size) return
    const gRef = refOf(r), gT = timeOf(r)
    const isDep = list.some(w => {
      if (!_isWrite(w.method) || refOf(w) !== gRef || timeOf(w) < gT) return false
      const wIds = idsIn(`${w.url || ''} ${w.requestBody || ''}`)
      for (const id of wIds) if (respIds.has(id)) return true
      return false
    })
    if (isDep) reasons[i] = { tag: 'dependency', pick: true }
  })

  return { indices: list.map((_, i) => i).filter(i => reasons[i].pick), reasons }
}
const _API_REASON_BADGE = {
  write: { label: '写操作', color: '#0ea5a0', bg: '#e0f7f6' },
  dependency: { label: '依赖', color: '#7c5cbf', bg: 'rgba(124,92,191,0.1)' },
  noise: { label: '噪音', color: '#c9cdd4', bg: 'transparent' },
}
const scenarioStatusMap = {
  draft: { label: '草稿', color: '#86909c', bg: 'rgba(0,0,0,0.02)' },
  debugging: { label: '调试中', color: '#faad14', bg: '#fffbe6' },
  completed: { label: '已完成', color: '#0ea5a0', bg: '#e0f7f6' },
}
// 状态体系 v2
const lifecycleMap = {
  draft: { label: '草稿', color: '#86909c', bg: 'rgba(0,0,0,0.03)' },
  done: { label: '完成', color: '#0ea5a0', bg: '#e0f7f6' },
  deprecated: { label: '废弃', color: '#e8453c', bg: '#fff2f0' },
}
// 三维统一状态（手动/UI/接口 共用）
const dimStatusMap = {
  not_started: { label: '未开始', color: '#c9cdd4', bg: 'rgba(0,0,0,0.02)' },
  draft: { label: '草稿', color: '#86909c', bg: 'rgba(0,0,0,0.03)' },
  debugging: { label: '调试中', color: '#faad14', bg: '#fffbe6' },
  pending_review: { label: '待审', color: '#4e8af0', bg: '#eef4ff' },
  executable: { label: '可执行', color: '#0ea5a0', bg: '#e0f7f6' },
  needs_fix: { label: '待修改', color: '#e8453c', bg: '#fff2f0' },
}
const DIM_STATUS_KEYS = ['not_started', 'draft', 'debugging', 'pending_review', 'executable', 'needs_fix']

function InlineProp({ icon, value, color, bg, children }) {
  const [open, setOpen] = useState(false)
  return (
    <Popover open={open} onOpenChange={setOpen} trigger="click" placement="bottomLeft"
      content={<div style={{ minWidth: 150 }} onClick={e => e.stopPropagation()}>{children}</div>}
      arrow={false} styles={{ body: { padding: 8 } }}>
      <div style={{
        display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 10px 2px 6px',
        borderRadius: 12, fontSize: 12, cursor: 'pointer', transition: 'all 0.15s',
        background: bg || 'rgba(0,0,0,0.02)', color: color || '#4e5969', border: '1px solid transparent',
        userSelect: 'none', lineHeight: '22px',
      }}
        onMouseEnter={e => e.currentTarget.style.borderColor = 'rgba(0,0,0,0.06)'}
        onMouseLeave={e => e.currentTarget.style.borderColor = 'transparent'}>
        {icon && <span style={{ fontSize: 11, color: color || '#86909c', display: 'flex' }}>{icon}</span>}
        <span style={{ fontWeight: 500, color: color || '#4e5969' }}>{value}</span>
      </div>
    </Popover>
  )
}

function ReadonlyProp({ icon, label, value, bg }) {
  return (
    <Tooltip title={label}>
      <div style={{
        display: 'inline-flex', alignItems: 'center', gap: 4, padding: '2px 10px 2px 6px',
        borderRadius: 12, fontSize: 12, background: bg || 'rgba(0,0,0,0.02)', lineHeight: '22px',
      }}>
        {icon && <span style={{ fontSize: 11, color: '#86909c', display: 'flex' }}>{icon}</span>}
        {label && <span style={{ color: '#86909c' }}>{label}</span>}
        <span style={{ fontWeight: 500, color: '#4e5969' }}>{value}</span>
      </div>
    </Tooltip>
  )
}

function DropdownList({ items, activeKey, onSelect }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      {items.map(item => (
        <div key={item.key} onClick={() => onSelect(item.key)} style={{
          padding: '6px 12px', borderRadius: 12, cursor: 'pointer', fontSize: 13,
          display: 'flex', alignItems: 'center', gap: 8,
          background: activeKey === item.key ? '#e0f7f6' : 'transparent',
          fontWeight: activeKey === item.key ? 600 : 400,
        }}
          onMouseEnter={e => e.currentTarget.style.background = 'rgba(0,0,0,0.02)'}
          onMouseLeave={e => e.currentTarget.style.background = activeKey === item.key ? '#e0f7f6' : 'transparent'}>
          {item.dot && <span style={{ width: 8, height: 8, borderRadius: item.dot === 'circle' ? '50%' : 2, background: item.color, flexShrink: 0 }} />}
          {item.icon && <span>{item.icon}</span>}
          {item.label}
        </div>
      ))}
    </div>
  )
}

function findFolderPath(tree, targetId) {
  for (const node of tree) {
    if (node.id === targetId) return node.path || node.name
    if (node.children?.length) {
      const found = findFolderPath(node.children, targetId)
      if (found) return found
    }
  }
  return null
}

function StepTable({ steps, updateStep, addStep, removeStep }) {
  const [actionPct, setActionPct] = useState(60)
  const dragging = useRef(false)
  const tableRef = useRef(null)

  useEffect(() => {
    const onMove = e => {
      if (!dragging.current || !tableRef.current) return
      const rect = tableRef.current.getBoundingClientRect()
      const fixedLeft = 24 + 28 + 30 // drag-handle + seq + gaps
      const fixedRight = 32 + 10 // delete btn + gap
      const available = rect.width - fixedLeft - fixedRight
      const x = e.clientX - rect.left - fixedLeft
      const pct = Math.max(25, Math.min(75, (x / available) * 100))
      setActionPct(pct)
    }
    const onUp = () => { dragging.current = false; document.body.style.cursor = '' }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => { window.removeEventListener('mousemove', onMove); window.removeEventListener('mouseup', onUp) }
  }, [])

  const startDrag = e => { e.preventDefault(); dragging.current = true; document.body.style.cursor = 'col-resize' }

  return (
    <div ref={tableRef} style={{ borderRadius: 12, border: '1px solid rgba(0,0,0,0.04)', overflow: 'hidden' }}>
      <div style={{
        display: 'flex', gap: 10, padding: '6px 14px', fontSize: 12, fontWeight: 600,
        background: 'rgba(0,0,0,0.02)', color: '#86909c', borderBottom: '1px solid rgba(0,0,0,0.04)', alignItems: 'center',
      }}>
        <span style={{ width: 24, flexShrink: 0 }}></span>
        <span style={{ width: 28, flexShrink: 0 }}>#</span>
        <span style={{ flex: `${actionPct} 0 0`, minWidth: 0 }}>操作步骤</span>
        <div onMouseDown={startDrag}
          style={{ width: 6, flexShrink: 0, cursor: 'col-resize', alignSelf: 'stretch', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <div style={{ width: 2, height: 14, borderRadius: 1, background: 'rgba(0,0,0,0.12)' }} />
        </div>
        <span style={{ flex: `${100 - actionPct} 0 0`, minWidth: 0 }}>预期结果</span>
        <span style={{ width: 32, flexShrink: 0 }}></span>
      </div>
      {steps.map((s, i) => (
        <div key={i} style={{
          display: 'flex', gap: 10, padding: '8px 14px', fontSize: 13,
          background: i % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.015)',
          borderBottom: i < steps.length - 1 ? '1px solid rgba(0,0,0,0.03)' : 'none', alignItems: 'flex-start',
        }}>
          <HolderOutlined style={{ color: 'rgba(0,0,0,0.15)', cursor: 'grab', flexShrink: 0, marginTop: 6 }} />
          <span style={{
            width: 28, height: 24, borderRadius: 12, background: '#e0f7f6', color: '#0ea5a0',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 600, fontSize: 12, flexShrink: 0, marginTop: 2,
          }}>{s.seq}</span>
          <Input.TextArea value={s.action} onChange={e => updateStep(i, 'action', e.target.value)}
            placeholder="描述操作步骤..." variant="borderless" autoSize={{ minRows: 1, maxRows: 8 }}
            style={{ flex: `${actionPct} 0 0`, minWidth: 0, fontSize: 13, resize: 'none' }}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey && i === steps.length - 1 && s.action.trim()) {
                e.preventDefault(); addStep()
                setTimeout(() => { const inputs = document.querySelectorAll('[placeholder="描述操作步骤..."]'); inputs[inputs.length - 1]?.focus() }, 50)
              }
            }} />
          <div onMouseDown={startDrag}
            style={{ width: 6, flexShrink: 0, cursor: 'col-resize', alignSelf: 'stretch' }} />
          <Input.TextArea value={s.expected || ''} onChange={e => updateStep(i, 'expected', e.target.value)}
            placeholder="预期结果..." variant="borderless" autoSize={{ minRows: 1, maxRows: 8 }}
            style={{ flex: `${100 - actionPct} 0 0`, minWidth: 0, fontSize: 13, color: '#86909c', resize: 'none' }} />
          <Button type="text" danger size="small" icon={<DeleteOutlined />}
            onClick={() => removeStep(i)} disabled={steps.length <= 1}
            style={{ flexShrink: 0, opacity: steps.length <= 1 ? 0.3 : 1, marginTop: 2 }} />
        </div>
      ))}
    </div>
  )
}

function ScenarioStepsView({ steps, extraCol, extraColLabel, extraPlaceholder, extraColor }) {
  if (!steps?.length) return <Empty description="暂无步骤" image={Empty.PRESENTED_IMAGE_SIMPLE} style={{ padding: 24 }} />
  return (
    <div style={{ borderRadius: 12, border: '1px solid rgba(0,0,0,0.04)', overflow: 'hidden' }}>
      <div style={{
        display: 'flex', gap: 10, padding: '6px 14px', fontSize: 12, fontWeight: 600,
        background: 'rgba(0,0,0,0.02)', color: '#86909c', borderBottom: '1px solid rgba(0,0,0,0.04)', alignItems: 'center',
      }}>
        <span style={{ width: 28, flexShrink: 0 }}>#</span>
        <span style={{ width: 52, flexShrink: 0 }}>阶段</span>
        <span style={{ flex: 2 }}>操作步骤</span>
        {extraCol && <span style={{ flex: 1 }}>{extraColLabel}</span>}
        <span style={{ flex: 1 }}>预期结果</span>
      </div>
      {steps.map((s, i) => (
        <div key={i} style={{
          display: 'flex', gap: 10, padding: '8px 14px', fontSize: 13,
          background: i % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.015)',
          borderBottom: i < steps.length - 1 ? '1px solid rgba(0,0,0,0.03)' : 'none', alignItems: 'center',
        }}>
          <span style={{
            width: 28, height: 24, borderRadius: 12, background: '#e0f7f6', color: '#0ea5a0',
            display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 600, fontSize: 12, flexShrink: 0,
          }}>{s.seq || i + 1}</span>
          {s.phase ? (
            <span style={{
              width: 52, flexShrink: 0, fontSize: 11, fontWeight: 500, textAlign: 'center',
              padding: '2px 0', borderRadius: 12,
              background: `${phaseColor[s.phase] || '#86909c'}15`, color: phaseColor[s.phase] || '#86909c',
            }}>{phaseLabel[s.phase] || s.phase}</span>
          ) : <span style={{ width: 52, flexShrink: 0 }} />}
          <span style={{ flex: 2 }}>{s.action || '-'}</span>
          {extraCol && (
            <span style={{ flex: 1, fontSize: 12, fontFamily: 'monospace', color: extraColor || '#0ea5a0' }}>
              {s[extraCol] || ''}
            </span>
          )}
          <span style={{ flex: 1, color: '#86909c' }}>{s.expected || '-'}</span>
        </div>
      ))}
    </div>
  )
}

function ScriptViewer({ scriptData, loading, error, onRetry }) {
  if (loading) return <div style={{ textAlign: 'center', padding: 48 }}><Spin tip="加载脚本中..." /></div>
  if (error) return (
    <div style={{ padding: 24, textAlign: 'center' }}>
      <div style={{ color: '#e8453c', marginBottom: 12 }}>{error}</div>
      <Button size="small" onClick={onRetry}>重试</Button>
    </div>
  )
  if (!scriptData) return null
  return (
    <div>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '8px 16px', background: 'rgba(0,0,0,0.02)', borderBottom: '1px solid rgba(0,0,0,0.04)', fontSize: 12,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <FileTextOutlined style={{ color: '#86909c' }} />
          <span style={{ fontFamily: 'monospace', color: '#4e5969' }}>{scriptData.filePath}</span>
          {scriptData.funcName && <Tag color="blue" style={{ fontSize: 11, margin: 0 }}>{scriptData.funcName}</Tag>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Tag style={{ fontSize: 11, margin: 0, fontFamily: 'monospace' }}>{scriptData.commitSha?.substring(0, 8)}</Tag>
          <Tooltip title="复制脚本内容">
            <Button type="text" size="small" icon={<CopyOutlined />} onClick={() => {
              copyToClipboard(scriptData.content)
              message.success('已复制到剪贴板')
            }} />
          </Tooltip>
        </div>
      </div>
      <div style={{ maxHeight: 500, overflow: 'auto', background: '#1e1e1e' }}>
        <pre style={{
          margin: 0, padding: '12px 0', fontSize: 13, lineHeight: 1.6,
          fontFamily: "'JetBrains Mono', 'Fira Code', 'Consolas', monospace", color: '#d4d4d4',
        }}>
          {scriptData.content.split('\n').map((line, i) => {
            const fn = scriptData.funcName
            const isTarget = fn && (line.includes(`def ${fn}`) || line.includes(`async def ${fn}`))
            return (
              <div key={i} style={{
                display: 'flex',
                background: isTarget ? 'rgba(255,213,79,0.15)' : 'transparent',
                borderLeft: isTarget ? '3px solid #ffd54f' : '3px solid transparent',
              }}>
                <span style={{ display: 'inline-block', width: 48, textAlign: 'right', paddingRight: 12, color: '#858585', userSelect: 'none', flexShrink: 0 }}>{i + 1}</span>
                <code style={{ whiteSpace: 'pre', flex: 1, paddingRight: 16 }}>{line}</code>
              </div>
            )
          })}
        </pre>
      </div>
    </div>
  )
}

function ScenarioCard({ scenario, type, accentColor, icon, scriptContent, scriptLoading, scriptError, onLoadScript }) {
  if (!scenario) return (
    <Card styles={{ body: { padding: '16px 20px' } }}>
      <Empty description={`暂无${type === 'api' ? '接口' : 'UI'}测试场景`} image={Empty.PRESENTED_IMAGE_SIMPLE}>
        <div style={{ color: '#86909c', fontSize: 12 }}>
          通过 generate-test-suite 生成或手动导入 tea-cases.json 添加
        </div>
      </Empty>
    </Card>
  )
  const extraCol = type === 'api' ? 'apiEndpoint' : 'uiTarget'
  const extraLabel = type === 'api' ? '接口' : '页面/元素'
  return (
    <Card styles={{ body: { padding: '16px 20px' } }}>
      {/* 脚本引用 */}
      {scenario.scriptRefFile && (
        <div style={{ marginBottom: 16, padding: '8px 12px', background: 'rgba(0,0,0,0.02)', borderRadius: 12, display: 'flex', alignItems: 'center', gap: 8 }}>
          <CodeOutlined style={{ color: '#86909c' }} />
          <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#4e5969' }}>{scenario.scriptRefFile}</span>
          {scenario.scriptRefFunc && <Tag color={accentColor} style={{ fontSize: 11, margin: 0 }}>{scenario.scriptRefFunc}</Tag>}
        </div>
      )}

      {/* 步骤表 */}
      <div style={{ marginBottom: 16 }}>
        <h4 style={{ fontSize: 13, color: '#86909c', marginBottom: 8 }}>测试步骤</h4>
        <ScenarioStepsView steps={scenario.steps} extraCol={extraCol} extraColLabel={extraLabel} extraColor={accentColor} />
      </div>

      {/* 依赖参数 */}
      {scenario.variablesUsed?.length > 0 && (
        <div style={{ marginBottom: 16 }}>
          <h4 style={{ fontSize: 13, color: '#86909c', marginBottom: 8 }}>依赖参数</h4>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {scenario.variablesUsed.map((v, i) => (
              <Tag key={i} style={{ fontFamily: 'monospace', fontSize: 12, background: '#edf3ff', border: '1px solid rgba(78,138,240,0.3)', color: '#4e8af0', borderRadius: 12, padding: '2px 8px' }}>{v}</Tag>
            ))}
          </div>
        </div>
      )}

      {/* 脚本源码 */}
      {scenario.scriptRefFile && (
        <div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
            <h4 style={{ fontSize: 13, color: '#86909c', margin: 0 }}>脚本源码</h4>
            {!scriptContent && !scriptLoading && (
              <Button size="small" type="link" icon={<CodeOutlined />} onClick={onLoadScript}>加载脚本</Button>
            )}
          </div>
          <div style={{ borderRadius: 12, overflow: 'hidden', border: '1px solid rgba(0,0,0,0.04)' }}>
            <ScriptViewer scriptData={scriptContent} loading={scriptLoading} error={scriptError} onRetry={onLoadScript} />
          </div>
        </div>
      )}
    </Card>
  )
}

function generateUiCode(steps, title) {
  const lines = ['from playwright.sync_api import Page, expect', '', '']
  const fnName = 'test_' + (title || 'ui_scenario').replace(/[^a-zA-Z0-9一-龥]/g, '_').replace(/_+/g, '_').substring(0, 40).toLowerCase()
  lines.push(`def ${fnName}(page: Page):`)
  lines.push(`    """${title || 'UI 测试'}"""`)
  lines.push('')

  for (const s of steps) {
    const target = s.uiTarget || ''
    lines.push(`    # Step ${s.seq}: ${s.action || ''}`)
    if (target.startsWith('/') || target.startsWith('http')) {
      lines.push(`    page.goto("${target}")`)
    } else if (target) {
      lines.push(`    page.locator("${target}").click()`)
    }
    if (s.expected) lines.push(`    # 预期: ${s.expected}`)
    lines.push('')
  }
  return lines.join('\n')
}

// 「编排为接口测试」把该用例的流量写到分支级 api_test_scenarios 表(source_case_id=caseId),
// 一个用例 = 一个接口场景。CC 同步来的和手动建的是同一条，用同一套 apifox 式编辑器。
// 存储统一在 api_test_scenarios/api_test_steps（保住整链执行与测试报告），
// 界面统一用 ApiStepList，中间靠 apiStepAdapter 做字段互转。
function LinkedApiScenarios({ projectId, branchId, caseId, caseTitle, active, runEnv, onEnvChange, environments, onCountChange }) {
  const [scenario, setScenario] = useState(null)   // 本用例唯一那条（含 steps）
  const [loading, setLoading] = useState(false)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [creating, setCreating] = useState(false)
  const [stepResults, setStepResults] = useState([])   // 逐步执行详情
  const [showPanel, setShowPanel] = useState(false)
  const [reportId, setReportId] = useState(null)
  const [precheck, setPrecheck] = useState(null)   // 跑前前置资源预检结论

  const base = `/projects/${projectId}/branches/${branchId}/api-tests`

  const load = useCallback(async () => {
    if (!projectId || !branchId || !caseId) return
    setLoading(true)
    try {
      const res = await api.get(`${base}?source_case_id=${caseId}`)
      const list = res.data || []
      onCountChange?.(list.length)
      if (!list.length) { setScenario(null); return }
      // 理论上只会有一条；历史数据若有多条，取步骤最多的那条，避免丢内容
      const head = list.length === 1 ? list[0]
        : [...list].sort((a, b) => (b.stepCount || 0) - (a.stepCount || 0))[0]
      const d = await api.get(`${base}/${head.id}`)
      setScenario(d.data || null)
    } catch { /* ignore */ }
    finally { setLoading(false) }
  }, [projectId, branchId, caseId, base, onCountChange])

  useEffect(() => { if (active) load() }, [active, load])

  // 编辑器整体回调：节点数组落回 api_test_steps
  const saveNodes = async (nodes) => {
    if (!scenario) return
    const sid = scenario.id
    const prev = scenario.steps || []
    try {
      for (const n of nodes) {
        const patch = nodeToStepPatch(n)
        if (n.id) await api.put(`${base}/${sid}/steps/${n.id}`, patch)
        else await api.post(`${base}/${sid}/steps`, patch)
      }
      const keep = new Set(nodes.filter(n => n.id).map(n => n.id))
      for (const st of prev) {
        if (!keep.has(st.id)) await api.delete(`${base}/${sid}/steps/${st.id}`)
      }
      await load()
    } catch (e) { message.error(e?.message || '保存失败') }
  }

  const createOne = async () => {
    setCreating(true)
    try {
      await api.post(base, { title: caseTitle ? `[接口]${caseTitle}` : '接口场景', priority: 'P2', sourceCaseId: caseId })
      await load()
    } catch (e) { message.error(e?.message || '创建失败') }
    finally { setCreating(false) }
  }

  const run = () => {
    if (!scenario) return
    if (!runEnv) { message.warning('请先选择执行环境（需要 BASE_URL）'); return }
    setRunning(true); setResult(null); setStepResults([]); setReportId(null); setPrecheck(null); setShowPanel(true)
    api.stream(`${base}/run`, { scenarioIds: [scenario.id], envId: runEnv }, {
      onChunk: (data) => {
        // 逐步推进：只显示汇总的话，失败了也不知道是哪一步、为什么
        if (data.type === 'precheck_result') setPrecheck(data)
        if (data.type === 'step_result') setStepResults(prev => [...prev, data])
        if (data.type === 'scenario_done') setResult({ passed: data.passed, passCount: data.passCount, failCount: data.failCount })
        if (data.type === 'report_created') setReportId(data.reportId)
        if (data.type === 'run_done') setRunning(false)
      },
      onDone: () => { setRunning(false); load() },   // 重新拉回 lastResponse，面板里才能看请求/响应
      onError: (msg) => { message.error(msg || '运行失败'); setRunning(false) },
    })
  }

  return (
    <Card styles={{ body: { padding: '14px 16px' } }} style={{ marginBottom: 12, border: '1px solid rgba(14,165,160,0.25)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <ApiOutlined style={{ color: '#0ea5a0' }} />
          <span style={{ fontSize: 13, fontWeight: 600, color: '#1d2129' }}>接口场景</span>
          {scenario && <span style={{ fontFamily: 'monospace', fontSize: 11, color: '#86909c' }}>{scenario.code}</span>}
          {scenario && <span style={{ fontSize: 12, color: '#86909c' }}>{(scenario.steps || []).length} 个请求</span>}
          {result && (
            <Tag color={result.passed ? 'success' : 'error'} style={{ margin: 0, cursor: 'pointer' }}
              onClick={() => setShowPanel(true)}>
              {result.passed ? '通过' : '失败'} {result.passCount ?? 0}/{(result.passCount ?? 0) + (result.failCount ?? 0)} · 看详情
            </Tag>
          )}
        </div>
        <Space size={6}>
          <Select size="small" value={runEnv} onChange={onEnvChange} style={{ width: 170 }}
            popupMatchSelectWidth={false} placeholder="执行环境" options={buildEnvOptions(environments)} />
          <Button size="small" onClick={load} loading={loading}>刷新</Button>
          {scenario && (
            <Button size="small" type="primary" ghost icon={<PlayCircleOutlined />} loading={running}
              style={{ color: '#0ea5a0', borderColor: '#0ea5a0' }} onClick={run}>运行全部</Button>
          )}
        </Space>
      </div>

      {loading && !scenario ? (
        <div style={{ textAlign: 'center', padding: '24px 0' }}><Spin size="small" /></div>
      ) : !scenario ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="本用例还没有接口场景">
          <Button type="primary" icon={<PlusOutlined />} loading={creating} onClick={createOne}
            style={{ background: '#0ea5a0', borderColor: '#0ea5a0' }}>创建接口场景</Button>
        </Empty>
      ) : (
        <ApiStepList
          steps={scenarioToNodes(scenario.steps)}
          environments={environments}
          runEnv={runEnv}
          onChange={saveNodes}
          nodeTypes={['api']}   // api_test_steps 存不下控制流节点，只放 API 请求
        />
      )}

      {showPanel && precheck?.missing?.length > 0 && (
        // 前置资源探不到就当场说清楚，别让人对着后面一串"变量未解析"猜
        <Alert type="warning" showIcon style={{ marginTop: 10 }}
          message={`前置资源预检：${precheck.readyCount}/${precheck.total} 就绪，${precheck.missing.length} 个缺失`}
          description={
            <div style={{ fontSize: 12 }}>
              {precheck.missing.map(m => (
                <div key={m.name}><b>{m.name}</b> —— {m.reason}</div>
              ))}
              <div style={{ color: '#86909c', marginTop: 4 }}>
                引用这些资源的步骤会报「变量未解析」。请确认它们在所选环境确实存在，
                或改为在场景开头自建 + 末尾清理。
              </div>
            </div>
          } />
      )}

      {showPanel && (stepResults.length > 0 || running) && (
        <RunResultPanel
          results={stepResults}
          scenario={scenario}
          running={running}
          reportId={reportId}
          projectId={projectId}
          envName={(environments.find(e => e.id === runEnv) || {}).name}
          onClose={() => setShowPanel(false)}
        />
      )}
    </Card>
  )
}

function ScenarioEditor({
  scenario, setScenario, scenarioStatus, setScenarioStatus,
  isTemplate, setIsTemplate, type, accentColor,
  onImportTemplate, manualSteps, caseTitle,
  projectId, branchId, caseId,
  environments, runEnv, onEnvChange,
  onScriptSaved, linkedCount = 0,
}) {
  const extraCol = type === 'api' ? 'apiEndpoint' : 'uiTarget'
  const extraLabel = type === 'api' ? '接口端点' : '页面/元素'
  const [viewMode, setViewMode] = useState('steps')
  const [newVarInput, setNewVarInput] = useState('')
  const [debugRunning, setDebugRunning] = useState(false)
  const [debugResult, setDebugResult] = useState(null)
  const [aiGenerating, setAiGenerating] = useState(false)
  const [previewScreenshot, setPreviewScreenshot] = useState(null)
  const [stepHints, setStepHints] = useState({})
  const [showNoiseSteps, setShowNoiseSteps] = useState(false)
  const [selectedApis, setSelectedApis] = useState([])
  const [apiArranging, setApiArranging] = useState(false)
  const [expandedApi, setExpandedApi] = useState(null)  // 接口视图展开查看请求/响应详情的行索引
  const [debugHistory, setDebugHistory] = useState([])
  // 智能识别：从抓包里算出「编排一个场景」推荐勾选哪些接口 + 每条理由（写操作/依赖/噪音）
  const apiAnalysis = useMemo(() => analyzeApiRequests(debugResult?.captured_requests || []), [debugResult?.captured_requests])
  const scriptEditorRef = useRef(null)

  const loadDebugHistory = async () => {
    try {
      const res = await api.get(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/runs?type=ui&limit=10`)
      setDebugHistory((res.data || []).filter(r => r.status !== 'passed'))
    } catch { /* ignore */ }
  }

  const handleAiGenerate = async () => {
    if (type === 'api') return
    if (!runEnv) { message.warning('请先选择执行环境（需要 BASE_URL）'); return }
    const genStart = Date.now()
    setAiGenerating(true)
    setDebugResult({ status: 'running', _drawerOpen: true, steps: [] })
    setLiveSteps([])
    liveStepsRef.current = []

    const token = await getValidToken()
    const url = `/api/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/generate-stream?type=ui`
    const controller = new AbortController()
    abortRef.current = controller

    try {
      const response = await fetch(url, {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({ envId: runEnv, stepHints: Object.keys(stepHints).length ? stepHints : undefined }),
        signal: controller.signal,
      })
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let currentEvent = null

      const processChunk = async () => {
        const { done, value } = await reader.read()
        if (done) return
        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('event: ')) { currentEvent = line.slice(7).trim(); continue }
          if (line.startsWith('data: ') && currentEvent) {
            try {
              const data = JSON.parse(line.slice(6))
              if (currentEvent === 'step_start') {
                setLiveSteps(prev => { const n = [...prev, { ...data, status: 'running' }]; liveStepsRef.current = n; return n })
              } else if (currentEvent === 'step_done') {
                setLiveSteps(prev => {
                  const existing = prev.find(s => s.seq === data.seq)
                  const n = existing
                    ? prev.map(s => s.seq === data.seq ? { ...s, ...data } : s)
                    : [...prev, data]
                  liveStepsRef.current = n; return n
                })
              } else if (currentEvent === 'done') {
                const live = liveStepsRef.current;
                const screenshots = (data.results || [])
                  .filter(r => r.screenshot && r.status === 'failed')
                  .map((r, i) => ({ base64: r.screenshot, name: `步骤失败: ${r.step?.substring(0,30) || '未知'}` }));
                setDebugResult({ ...data, durationMs: data.durationMs ?? data.duration_ms ?? (Date.now() - genStart), steps: live.length > 0 ? live : data.results || [], screenshots, _drawerOpen: true });
                if (!scenario) {
                  setScenario({ steps: (manualSteps || []).map((s, i) => ({ seq: i + 1, action: s.action || '', expected: s.expected || '' })), variablesUsed: [] })
                }
                if (onScriptSaved) onScriptSaved()
                setTimeout(() => scriptEditorRef.current?.refresh(), 300)
                setAiGenerating(false)
                setLiveSteps([]); liveStepsRef.current = []
                if (data.all_passed) message.success('生成并验证全部通过！')
                else message.warning('部分步骤失败，查看详情')
                return
              }
            } catch {}
            currentEvent = null
          }
        }
        await processChunk()
      }
      await processChunk()
    } catch (e) {
      if (e.name !== 'AbortError') {
        message.error(e?.message || 'AI 生成失败')
      }
      setAiGenerating(false)
    }
  }

  // 流式运行脚本 — 实时推送步骤进度
  const [liveSteps, setLiveSteps] = useState([])
  const liveStepsRef = useRef([])
  const abortRef = useRef(null)

  const stopExecution = () => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setDebugRunning(false)
    setAiGenerating(false)
    setLiveSteps(prev => prev.map(s => s.status === 'running' ? { ...s, status: 'cancelled', error: '用户取消' } : s))
    setDebugResult(prev => prev ? { ...prev, status: 'cancelled' } : prev)
    message.info('已停止执行')
  }

  const runScriptWithStream = (onDone) => {
    const url = `/api/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/run-stream?type=ui`
    const controller = new AbortController()
    abortRef.current = controller
    setLiveSteps([])
    setDebugRunning(true)
    // 打开 Drawer 显示实时进度；保留生成时抓到的接口，别让「运行」把接口视图清空
    setDebugResult(prev => ({ status: 'running', _drawerOpen: true, steps: [], captured_requests: prev?.captured_requests || [] }))

    getValidToken().then(token => fetch(url, {
      method: 'POST',
      headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ envId: runEnv }),
      signal: controller.signal,
    })).then(response => {
      const reader = response.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''

      function processChunk() {
        reader.read().then(({ done, value }) => {
          if (done) return
          buffer += decoder.decode(value, { stream: true })
          const lines = buffer.split('\n')
          buffer = lines.pop() || ''

          let currentEvent = null
          for (const line of lines) {
            if (line.startsWith('event: ')) currentEvent = line.slice(7).trim()
            else if (line.startsWith('data: ') && currentEvent) {
              try {
                const data = JSON.parse(line.slice(6))
                if (currentEvent === 'step_start') {
                  setLiveSteps(prev => { const n = [...prev, { ...data, status: 'running' }]; liveStepsRef.current = n; return n })
                } else if (currentEvent === 'step_end') {
                  setLiveSteps(prev => { const n = prev.map(s => s.seq === data.seq ? { ...s, ...data } : s); liveStepsRef.current = n; return n })
                } else if (currentEvent === 'done') {
                  // 优先用 liveSteps（有完整步骤名），fallback 到 data.steps
                  const live = liveStepsRef.current
                  const steps = live.length > 0 ? live : (data.steps || [])
                  // 运行结果不含接口流量 → 保留生成时抓到的接口，别把「接口视图」清空
                  setDebugResult(prev => ({ ...data, steps, captured_requests: (data.captured_requests?.length ? data.captured_requests : prev?.captured_requests) || [], _drawerOpen: true }))
                  setDebugRunning(false)
                  setLiveSteps([]); liveStepsRef.current = []
                  scriptEditorRef.current?.refresh()
                  onDone?.(data)
                }
              } catch {}
              currentEvent = null
            }
          }
          processChunk()
        }).catch(() => { setDebugRunning(false) })
      }
      processChunk()
    }).catch(e => {
      setDebugResult({ status: 'error', errorSummary: e.message, _drawerOpen: true })
      setDebugRunning(false)
    })
  }

  const handleDebugRun = async () => {
    if (!runEnv) { message.warning('请先选择执行环境'); return }
    if (type === 'api') {
      // 接口类型走原来的同步方式
      setDebugRunning(true); setDebugResult(null)
      api.post(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/run?type=api`, { envId: runEnv })
        .then(res => setDebugResult({ ...(res.data || res), _drawerOpen: true }))
        .catch(e => setDebugResult({ status: 'error', errorSummary: e?.response?.data?.error?.message || e.message, _drawerOpen: true }))
        .finally(() => setDebugRunning(false))
      return
    }
    const doRun = () => runScriptWithStream((result) => {
      if (result.status === 'passed') message.success('验证通过！')
      else message.warning('验证失败，查看详情')
    })
    // UI 类型：跑前预检（缺全局前置 / 环境取不到鉴权 token 时提示，避免跑错环境后拿到晦涩报错）
    try {
      const pf = await api.get(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/preflight?type=ui&envId=${runEnv}`)
      const d = pf.data || {}
      const warns = []
      if (!d.ready) (d.missing || []).forEach(m => warns.push(`缺全局前置资源「${m.name}」（${m.reason || '不存在'}）`))
      if (d.scriptUsesToken && !d.tokenAcquired) warns.push('当前环境未能取得鉴权 token —— 脚本含鉴权 API 调用，很可能 401。请检查该环境的 BASE_URL / 登录配置(LOGIN_URL) / 账号密码是否正确，或换用脚本对应的环境。')
      if (warns.length) {
        Modal.confirm({
          title: '执行前检查',
          width: 520,
          content: <div style={{ marginTop: 8 }}>{warns.map((w, i) => <div key={i} style={{ marginBottom: 8, color: '#d46b08' }}>⚠️ {w}</div>)}</div>,
          okText: '仍要执行', cancelText: '取消',
          onOk: doRun,
        })
        return
      }
    } catch { /* 预检失败不阻断执行 */ }
    doRun()
  }

  const initScenario = (fromManual) => {
    let newSteps
    if (fromManual && manualSteps?.length) {
      newSteps = manualSteps.map((s, i) => ({
        seq: i + 1,
        phase: i === 0 ? 'setup' : i < manualSteps.length - 1 ? 'action' : 'verify',
        action: s.action || '',
        expected: s.expected || '',
        [extraCol]: '',
      }))
    } else {
      newSteps = [{ seq: 1, phase: 'action', action: '', expected: '', [extraCol]: '' }]
    }
    setScenario({ steps: newSteps, variablesUsed: [] })
  }

  if (!scenario) return (
    <Card styles={{ body: { padding: '24px 20px' } }}>
      {type !== 'api' && manualSteps?.length > 0 ? (
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 16, padding: '32px 0' }}>
          <DesktopOutlined style={{ fontSize: 40, color: 'rgba(124,92,191,0.25)' }} />
          <div style={{ fontSize: 14, color: '#4e5969', fontWeight: 500 }}>
            基于手动测试步骤（{manualSteps.length} 步）生成 Playwright 自动化脚本
          </div>
          <div style={{ fontSize: 12, color: '#86909c', maxWidth: 400, textAlign: 'center' }}>
            AI 将分析用例的操作步骤和预期结果，生成可执行的 Playwright Python 测试脚本，并在目标系统上运行验证
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 4 }}>
            <Select size="middle" value={runEnv} onChange={onEnvChange} style={{ width: 220 }}
              popupMatchSelectWidth={false}
              placeholder="选择执行环境" options={buildEnvOptions(environments)} />
            <Button type="primary" size="middle" icon={<ThunderboltOutlined />}
              loading={aiGenerating} disabled={!runEnv}
              onClick={handleAiGenerate}
              style={{ background: '#7c5cbf', borderColor: '#7c5cbf', height: 36 }}>
              AI 生成脚本
            </Button>
          </div>
          {!runEnv && <div style={{ fontSize: 12, color: '#c9cdd4' }}>请先选择环境（需要配置 BASE_URL 变量）</div>}
        </div>
      ) : type !== 'api' ? (
        <Empty description="该用例没有手动测试步骤，请先在「手动测试步骤」Tab 添加步骤" image={Empty.PRESENTED_IMAGE_SIMPLE} />
      ) : (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={linkedCount > 0
            // 上方已列出回推的编排场景，这里再说「暂无」会自相矛盾——
            // 两者不是一回事：上面是独立的接口场景，这里是用例自带的内嵌场景
            ? <span>上方已有 <b>{linkedCount}</b> 条由本用例编排的接口场景。<br />
                <span style={{ fontSize: 12, color: '#86909c' }}>这里是用例自带的内嵌场景（另一份，可选），一般不用再建</span></span>
            : '暂无接口测试场景'}
        >
          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
            <Space>
              <Button type={linkedCount > 0 ? 'default' : 'primary'} icon={<PlusOutlined />} onClick={() => initScenario(false)}>创建空白场景</Button>
              {manualSteps?.length > 0 && (
                <Button icon={<CopyOutlined />} onClick={() => initScenario(true)}>从手动步骤生成</Button>
              )}
            </Space>
            <Button type="link" size="small" icon={<ImportOutlined />} onClick={onImportTemplate}>从模板导入</Button>
          </div>
        </Empty>
      )}
    </Card>
  )

  const steps = scenario.steps || []
  const updateScenario = (patch) => setScenario(prev => ({ ...prev, ...patch }))
  const updateStepField = (idx, field, value) => {
    const newSteps = steps.map((s, i) => i === idx ? { ...s, [field]: value } : s)
    updateScenario({ steps: newSteps })
  }
  const addStep = () => updateScenario({
    steps: [...steps, { seq: steps.length + 1, phase: 'action', action: '', expected: '', [extraCol]: '' }]
  })
  const removeStep = (idx) => updateScenario({
    steps: steps.filter((_, i) => i !== idx).map((s, i) => ({ ...s, seq: i + 1 }))
  })

  const scVars = scenario.variablesUsed || []

  // UI 类型：加载最近一次执行结果 + 从 ui_scenario 恢复生成数据
  useEffect(() => {
    if (type !== 'api' && caseId) {
      // 从 ui_scenario 恢复上次生成的步骤和接口数据
      const uiData = scenario || {}
      if (uiData.stepHints && Object.keys(uiData.stepHints).length) {
        setStepHints(uiData.stepHints)
      }
      if (uiData.lastResults?.length > 0 && !debugResult) {
        const allPassed = uiData.lastResults.every(r => r.status === 'passed')
        setDebugResult({
          status: allPassed ? 'passed' : 'failed',
          steps: uiData.lastResults,
          captured_requests: uiData.capturedRequests || [],
        })
      }
      // 从最近执行记录补充（如果 ui_scenario 没有数据）
      if (!uiData.lastResults?.length && !debugResult) {
        api.get(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/runs?type=ui&limit=1`)
          .then(res => {
            const last = (res.data || [])[0]
            if (last) {
              setDebugResult({ ...last, durationMs: last.durationMs || last.duration_ms, errorSummary: last.errorSummary || last.error_summary })
            }
          }).catch(() => {})
      }
    }
  }, [caseId, type, scenario])

  // ── UI 类型：专用简洁布局 ──
  if (type !== 'api') {
    const passed = debugResult?.status === 'passed'
    return (
      <Card styles={{ body: { padding: '16px 20px' } }}>
        {/* 工具栏 */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
          <Space size={8}>
            <Select size="small" value={runEnv} onChange={onEnvChange} style={{ width: 180 }}
              popupMatchSelectWidth={false}
              placeholder="选择环境" options={buildEnvOptions(environments)} />
            <Button size="small" icon={<ThunderboltOutlined />}
              loading={aiGenerating} disabled={!runEnv}
              onClick={handleAiGenerate}
              style={{ borderColor: '#7c5cbf', color: '#7c5cbf' }}>
              {aiGenerating ? '生成中...' : 'AI 生成'}
            </Button>
            <Button size="small" type="primary" icon={<PlayCircleOutlined />}
              loading={debugRunning} disabled={!runEnv}
              onClick={handleDebugRun}
              style={{ background: '#7c5cbf', borderColor: '#7c5cbf' }}>
              运行验证
            </Button>
          </Space>
          <Space size={8}>
            <Button size="small" icon={<SaveOutlined />}
              onClick={() => scriptEditorRef.current?.save()}>
              保存
            </Button>
            <Button size="small" icon={<CopyOutlined />}
              onClick={() => scriptEditorRef.current?.copyCode()}>
              复制
            </Button>
            {/* 右侧状态标签 */}
            {debugResult && (
              <Tag color={passed ? undefined : 'error'}
                style={{ cursor: 'pointer', fontWeight: 600, fontSize: 12, padding: '2px 10px', margin: 0,
                  ...(passed ? { background: '#e0f7f6', color: '#0ea5a0', border: 'none' } : {}) }}
                onClick={() => setDebugResult(prev => prev ? { ...prev, _drawerOpen: true } : prev)}>
                {passed ? '✓ 通过' : '✗ 失败'} · {debugResult.durationMs != null ? `${(debugResult.durationMs / 1000).toFixed(1)}s` : ''}
                {debugResult.screenshots?.length > 0 ? ` · ${debugResult.screenshots.length} 截图` : ''}
              </Tag>
            )}
          </Space>
        </div>

        {/* 最近执行摘要 */}
        {debugResult && debugResult.status !== 'running' && (
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8, padding: '8px 12px', marginBottom: 8,
            borderRadius: 8, cursor: 'pointer',
            background: passed ? 'rgba(14,165,160,0.06)' : 'rgba(232,69,60,0.06)',
            border: passed ? '1px solid rgba(14,165,160,0.15)' : '1px solid rgba(232,69,60,0.15)',
          }} onClick={() => setDebugResult(prev => prev ? { ...prev, _drawerOpen: true } : prev)}>
            {passed ? <CheckCircleOutlined style={{ color: '#0ea5a0' }} /> : <WarningOutlined style={{ color: '#e8453c' }} />}
            <span style={{ fontSize: 13, fontWeight: 500, color: passed ? '#0ea5a0' : '#e8453c' }}>
              {passed ? '验证通过' : '验证失败'}
            </span>
            {debugResult.durationMs && <span style={{ fontSize: 12, color: '#86909c' }}>{(debugResult.durationMs / 1000).toFixed(1)}s</span>}
            {debugResult.steps?.length > 0 && (
              <span style={{ fontSize: 12, color: '#86909c' }}>
                {debugResult.steps.filter(s => s.status === 'passed').length}/{debugResult.steps.length} 步通过
              </span>
            )}
            {debugResult.captured_requests?.length > 0 && (
              <span style={{ fontSize: 12, color: '#86909c' }}>{debugResult.captured_requests.length} 个接口</span>
            )}
            <span style={{ fontSize: 11, color: '#c9cdd4', marginLeft: 'auto' }}>点击查看详情</span>
          </div>
        )}

        {/* 三视图切换 */}
        <Tabs size="small" defaultActiveKey="steps" style={{ marginBottom: 0 }}
          items={[
            { key: 'steps', label: `执行轨迹${(liveSteps.length || debugResult?.steps?.length) ? ` (${liveSteps.length > 0 ? liveSteps.length : debugResult.steps.length})` : ''}`, children: (
              <div style={{ padding: '12px 0' }}>
                {(debugResult?.steps || liveSteps || []).length > 0 ? (() => {
                  const allSteps = liveSteps.length > 0 ? liveSteps : (debugResult?.steps || [])
                  // 探索类噪音（快照/JS求值/查找/网络/关闭浏览器/Glob）默认折叠，突出真实动作；失败步骤始终显示
                  const NOISE = /^(获取页面快照|执行 JS|关闭浏览器|Glob|browser_find|browser_network|browser_snapshot|browser_evaluate|Read|Bash)/
                  const isNoise = (s) => NOISE.test(s.step_name || s.action || s.step || '') && s.status !== 'failed'
                  const hiddenCount = allSteps.filter(isNoise).length
                  const shownSteps = showNoiseSteps ? allSteps : allSteps.filter(s => !isNoise(s))
                  return (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
                    <div style={{ padding: '6px 12px', marginBottom: 6, fontSize: 12, color: '#86909c', display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', background: 'rgba(124,92,191,0.04)', borderRadius: 6 }}>
                      <Tag color="purple" style={{ margin: 0 }}>本次执行轨迹 · {allSteps.length} 步</Tag>
                      <span>脚本逻辑 {scenario?.steps?.length || 0} 步</span>
                      <span style={{ color: '#c9cdd4' }}>Tab 角标是「脚本逻辑步骤」；此处是「实际执行轨迹」（含探索动作），两者不同属正常</span>
                    </div>
                    {hiddenCount > 0 && (
                      <div style={{ padding: '4px 12px', marginBottom: 4, fontSize: 12, color: '#86909c' }}>
                        显示 {shownSteps.length} 个关键步骤
                        <Button type="link" size="small" style={{ fontSize: 12, padding: '0 4px' }}
                          onClick={() => setShowNoiseSteps(v => !v)}>
                          {showNoiseSteps ? '收起' : `展开 ${hiddenCount} 条探索日志`}
                        </Button>
                      </div>
                    )}
                    {shownSteps.map((s, i) => {
                      const ok = s.status === 'passed'
                      const isRunning = s.status === 'running'
                      const phase = s.step_phase || s.phase
                      const name = s.step_name || s.action || s.step || `步骤 ${i + 1}`
                      const error = s.error_summary || s.error
                      const phaseEmoji = { setup: '🔧', action: '👆', verify: '✅' }
                      return (
                        <div key={i} style={{ display: 'flex', gap: 10, padding: '8px 12px', borderRadius: 8, background: i % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.015)' }}>
                          <span style={{ fontSize: 18, width: 28, textAlign: 'center', flexShrink: 0 }}>
                            {isRunning ? '⏳' : ok ? (phaseEmoji[phase] || '✅') : '❌'}
                          </span>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontSize: 13, fontWeight: 500, color: isRunning ? '#7c5cbf' : ok ? '#1d2129' : '#e8453c' }}>
                              {name}
                              <span style={{
                                fontSize: 11, padding: '1px 6px', borderRadius: 8, marginLeft: 8, fontWeight: 500,
                                background: isRunning ? '#f3f0ff' : ok ? '#e0f7f6' : '#fff2f0',
                                color: isRunning ? '#7c5cbf' : ok ? '#0ea5a0' : '#e8453c',
                              }}>
                                {isRunning ? '执行中' : ok ? '通过' : '失败'}
                              </span>
                            </div>
                            {error && <div style={{ marginTop: 4, fontSize: 12, color: '#e8453c', lineHeight: 1.4 }}>{error.substring(0, 200)}</div>}
                            {!ok && !isRunning && s.failure_type && (
                              <Tag style={{ marginTop: 4, fontSize: 11 }} color={
                                s.failure_type === 'system_bug' ? 'red' : s.failure_type === 'dependency' ? 'orange' : s.failure_type === 'case_expired' ? 'blue' : 'default'
                              }>{
                                {script_bug: '脚本问题', system_bug: '系统Bug', case_expired: '用例过期', dependency: '缺少依赖'}[s.failure_type] || s.failure_type
                              }</Tag>
                            )}
                            {!ok && !isRunning && (
                              <div style={{ marginTop: 4 }}>
                                <Input size="small" placeholder="输入指导（如：改成验证运行中）"
                                  value={stepHints[i] || ''} onChange={e => setStepHints(prev => ({ ...prev, [i]: e.target.value }))}
                                  style={{ width: '100%', fontSize: 12 }}
                                  suffix={stepHints[i] ? <span style={{ fontSize: 11, color: '#7c5cbf', cursor: 'pointer' }} onClick={() => message.info('指导已记录，重新生成时将应用')}>已记录</span> : null}
                                />
                              </div>
                            )}
                          </div>
                          {s.duration_ms != null && <span style={{ fontSize: 11, color: '#c9cdd4', flexShrink: 0 }}>{s.duration_ms >= 1000 ? `${(s.duration_ms / 1000).toFixed(1)}s` : `${s.duration_ms}ms`}</span>}
                        </div>
                      )
                    })}
                  </div>
                  )})() : (
                  <div style={{ padding: 32, textAlign: 'center', color: '#c9cdd4' }}>点击「AI 生成」生成脚本后查看执行步骤</div>
                )}
              </div>
            )},
            { key: 'script', label: '脚本视图', children: (
              <div style={{ position: 'relative' }}>
                {aiGenerating && (
                  <div style={{
                    position: 'absolute', inset: 0, zIndex: 10,
                    display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12,
                    background: 'rgba(30,30,46,0.9)', borderRadius: 8,
                  }}>
                    <Spin size="large" />
                    <div style={{ color: '#cdd6f4', fontSize: 14 }}>AI 正在逐步生成脚本...</div>
                  </div>
                )}
                <ScriptEditor
                  ref={scriptEditorRef}
                  projectId={projectId} branchId={branchId} caseId={caseId}
                  scriptType="ui" accentColor="#7c5cbf"
                  autoGenerateCode={generateUiCode(steps, caseTitle)}
                  onScriptSaved={onScriptSaved}
                  envId={runEnv}
                  hideToolbar
                />
              </div>
            )},
            { key: 'api', label: `接口视图${debugResult?.captured_requests?.length ? ` (${debugResult.captured_requests.length})` : ''}`, children: (
              debugResult?.captured_requests?.length > 0 ? (
                <div style={{ padding: '8px 0' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <span style={{ fontSize: 12, color: '#86909c' }}>
                      勾选需要的接口，编排为接口测试场景（可先点「智能识别」自动挑出核心接口再微调）
                      {selectedApis.length > 0 && <span style={{ color: '#7c5cbf', fontWeight: 600 }}> · 已选 {selectedApis.length} 个</span>}
                    </span>
                    <Space size={8}>
                      {apiAnalysis.indices.length > 0 && (
                        <Tooltip title="按「写操作 + 依赖回溯」自动挑出编排一个场景真正需要的接口，勾选后可再人工微调">
                          <Button size="small" onClick={() => setSelectedApis(apiAnalysis.indices)}>
                            🎯 智能识别（{apiAnalysis.indices.length}）
                          </Button>
                        </Tooltip>
                      )}
                      {selectedApis.length > 0 && (
                        <Button size="small" onClick={() => setSelectedApis([])}>取消选择</Button>
                      )}
                      <Button size="small" type="primary" disabled={selectedApis.length === 0} loading={apiArranging}
                        style={{ background: '#0ea5a0', borderColor: '#0ea5a0' }}
                        onClick={async () => {
                          setApiArranging(true)
                          try {
                            const selected = selectedApis.map(idx => debugResult.captured_requests[idx])
                            // 传完整请求信息（含 query/请求体/响应样例），让生成的接口测试有真实字段而非只有 URL
                            const apiInfo = selected.map(r => {
                              const parts = [`${r.method} ${r.url} → ${r.status}`]
                              if (r.queryParams && Object.keys(r.queryParams).length) parts.push(`  query: ${JSON.stringify(r.queryParams)}`)
                              if (r.requestContentType) parts.push(`  reqContentType: ${r.requestContentType}`)
                              if (r.requestBody) parts.push(`  reqBody: ${String(r.requestBody).slice(0, 1500)}`)
                              if (r.responseBody) parts.push(`  respSample: ${String(r.responseBody).slice(0, 1200)}`)
                              return parts.join('\n')
                            }).join('\n\n')
                            // 该端点是 SSE 流式（AI 逐条生成，多接口耗时较长）——必须按流消费，
                            // 不能用普通 POST（会误报「编排失败」，实际后端已生成）。
                            const token = await getValidToken()
                            const resp = await fetch(`/api/projects/${projectId}/branches/${branchId}/api-tests/generate`, {
                              method: 'POST',
                              headers: { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' },
                              body: JSON.stringify({ apiInfo, folderName: caseTitle || 'UI流量提取', caseId }),
                            })
                            if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
                            const reader = resp.body.getReader()
                            const decoder = new TextDecoder()
                            let buf = '', createdCount = 0, errMsg = null, finished = false, extractedVars = []
                            const hide = message.loading(`正在编排 ${selected.length} 个接口为测试场景（AI 生成中，请稍候）...`, 0)
                            try {
                              while (true) {
                                const { done, value } = await reader.read()
                                if (value) buf += decoder.decode(value, { stream: true })
                                const lines = buf.split('\n'); buf = done ? '' : (lines.pop() || '')
                                for (const line of lines) {
                                  if (!line.startsWith('data:')) continue
                                  try {
                                    const ev = JSON.parse(line.slice(5))
                                    if (ev.type === 'variables_extracted') { extractedVars = ev.names || extractedVars }
                                    else if (ev.type === 'done') { createdCount = (ev.scenarioIds || []).length || createdCount; extractedVars = ev.extractedVariables || extractedVars; finished = true }
                                    else if (ev.type === 'error') errMsg = ev.message || '生成失败'
                                  } catch {}
                                }
                                if (done) break
                              }
                            } finally { hide() }
                            if (errMsg && !finished) { message.error(`编排失败：${errMsg}`) }
                            else {
                              const varTip = extractedVars.length ? `，并自动提取 ${extractedVars.length} 个场景变量（${extractedVars.slice(0, 3).join('、')}${extractedVars.length > 3 ? '…' : ''}，UI/接口共用，见「场景变量」）` : ''
                              message.success(`已编排为 ${createdCount || selected.length} 个接口测试场景，见「接口测试」${varTip}`)
                              setSelectedApis([])
                            }
                          } catch (e) {
                            message.error(e?.message || '编排失败')
                          } finally {
                            setApiArranging(false)
                          }
                        }}>
                        编排为接口测试
                      </Button>
                    </Space>
                  </div>
                  <div style={{ borderRadius: 8, border: '1px solid rgba(0,0,0,0.04)', overflow: 'hidden' }}>
                    <div style={{ display: 'flex', padding: '6px 12px', fontSize: 12, fontWeight: 600, color: '#86909c', background: 'rgba(0,0,0,0.02)', borderBottom: '1px solid rgba(0,0,0,0.04)', alignItems: 'center' }}>
                      <span style={{ width: 32 }}>
                        <input type="checkbox"
                          checked={selectedApis.length === debugResult.captured_requests.length && selectedApis.length > 0}
                          onChange={e => setSelectedApis(e.target.checked ? debugResult.captured_requests.map((_, i) => i) : [])}
                          style={{ cursor: 'pointer' }} />
                      </span>
                      <span style={{ width: 60 }}>方法</span>
                      <span style={{ flex: 1 }}>URL</span>
                      <span style={{ width: 60, textAlign: 'right' }}>状态</span>
                    </div>
                    {debugResult.captured_requests.map((r, i) => {
                      const reqBody = r.requestBody || r.post_data || ''
                      const respBody = r.responseBody || ''
                      const hasDetail = reqBody || respBody || (r.queryParams && Object.keys(r.queryParams).length)
                      const expanded = expandedApi === i
                      return (
                      <div key={i}>
                      <div style={{
                        display: 'flex', padding: '4px 12px', fontSize: 12, alignItems: 'center',
                        borderBottom: '1px solid rgba(0,0,0,0.02)',
                        background: selectedApis.includes(i) ? 'rgba(14,165,160,0.04)' : i % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.01)',
                        cursor: 'pointer',
                      }} onClick={() => setSelectedApis(prev => prev.includes(i) ? prev.filter(x => x !== i) : [...prev, i])}>
                        <span style={{ width: 32 }}>
                          <input type="checkbox" checked={selectedApis.includes(i)} readOnly style={{ cursor: 'pointer' }} />
                        </span>
                        <Tag color={r.method === 'GET' ? 'blue' : r.method === 'POST' ? 'green' : r.method === 'PUT' ? 'orange' : r.method === 'DELETE' ? 'red' : 'default'}
                          style={{ width: 50, textAlign: 'center', margin: 0, fontSize: 11 }}>{r.method}</Tag>
                        {(() => {
                          const rn = apiAnalysis.reasons[i]
                          const badge = rn && _API_REASON_BADGE[rn.tag]
                          if (!badge) return null
                          return (
                            <span title={rn.tag === 'write' ? '写操作，场景核心，已推荐' : rn.tag === 'dependency' ? '被写操作依赖（提供了其用到的 ID），已推荐' : '后台轮询/列表刷新等噪音，未推荐'}
                              style={{ marginLeft: 6, padding: '0 6px', height: 18, lineHeight: '18px', borderRadius: 9, fontSize: 10, fontWeight: 600, color: badge.color, background: badge.bg, border: rn.tag === 'noise' ? '1px solid rgba(0,0,0,0.06)' : 'none', whiteSpace: 'nowrap' }}>
                              {badge.label}
                            </span>
                          )
                        })()}
                        <div style={{ flex: 1, marginLeft: 8, minWidth: 0 }}>
                          <div style={{ fontFamily: 'monospace', fontSize: 11, color: '#4e5969', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {(r.path || r.url || '').replace(/^https?:\/\/[^/]+/, '')}
                          </div>
                          {reqBody && (
                            <div style={{ fontSize: 10, color: '#86909c', fontFamily: 'monospace', marginTop: 2, maxHeight: 40, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                              {String(reqBody).substring(0, 120)}{String(reqBody).length > 120 ? '...' : ''}
                            </div>
                          )}
                        </div>
                        {hasDetail && (
                          <a style={{ fontSize: 11, marginRight: 10, color: expanded ? '#0ea5a0' : '#86909c', whiteSpace: 'nowrap' }}
                            onClick={e => { e.stopPropagation(); setExpandedApi(expanded ? null : i) }}>
                            {expanded ? '收起' : '详情'}
                          </a>
                        )}
                        <Tag color={r.status < 400 ? undefined : 'error'} style={{ margin: 0, fontSize: 11, ...(r.status < 400 && r.status >= 0 ? { background: '#e0f7f6', color: '#0ea5a0', border: 'none' } : {}) }}>{r.status}</Tag>
                      </div>
                      {expanded && (
                        <div style={{ padding: '10px 12px 12px 44px', background: 'rgba(14,165,160,0.03)', borderBottom: '1px solid rgba(0,0,0,0.04)', fontSize: 12 }}>
                          <div style={{ color: '#4e5969', wordBreak: 'break-all', marginBottom: 6, fontFamily: 'monospace', fontSize: 11 }}>
                            <b style={{ color: '#1d2129' }}>{r.method}</b> {r.url}
                          </div>
                          {r.queryParams && Object.keys(r.queryParams).length > 0 && (
                            <div style={{ marginBottom: 8 }}>
                              <div style={{ color: '#86909c', fontWeight: 600, marginBottom: 2 }}>Query 参数</div>
                              <pre style={{ margin: 0, padding: 8, background: '#fff', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 6, fontSize: 11, maxHeight: 140, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{JSON.stringify(r.queryParams, null, 2)}</pre>
                            </div>
                          )}
                          {reqBody ? (
                            <div style={{ marginBottom: 8 }}>
                              <div style={{ color: '#86909c', fontWeight: 600, marginBottom: 2 }}>请求体{r.requestContentType ? ` (${r.requestContentType})` : ''}</div>
                              <pre style={{ margin: 0, padding: 8, background: '#fff', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 6, fontSize: 11, maxHeight: 200, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{prettyJson(reqBody)}</pre>
                            </div>
                          ) : (r.method !== 'GET' && <div style={{ color: '#c9cdd4', marginBottom: 8 }}>（无请求体）</div>)}
                          {respBody ? (
                            <div>
                              <div style={{ color: '#86909c', fontWeight: 600, marginBottom: 2 }}>响应体{r.responseContentType ? ` (${r.responseContentType})` : ''}{r.status >= 0 ? ` · ${r.status}` : ''}</div>
                              <pre style={{ margin: 0, padding: 8, background: '#fff', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 6, fontSize: 11, maxHeight: 240, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>{prettyJson(respBody)}</pre>
                            </div>
                          ) : (
                            <div style={{ color: '#c9cdd4' }}>（无响应体{r.status < 0 ? '，请求未完成' : ''}）</div>
                          )}
                        </div>
                      )}
                      </div>
                      )
                    })}
                  </div>
                </div>
              ) : (
                <div style={{ padding: 32, textAlign: 'center', color: '#c9cdd4' }}>
                  点击「AI 生成」后，执行过程中的 API 请求将在此展示
                </div>
              )
            )},
          ]}
        />

        {/* 执行结果抽屉 */}
        <Drawer
          title={null}
          placement="right"
          width={580}
          open={!!debugResult?._drawerOpen}
          onClose={() => setDebugResult(prev => prev ? { ...prev, _drawerOpen: false } : null)}
          afterOpenChange={(open) => { if (open) loadDebugHistory() }}
          styles={{ body: { padding: 0 } }}
        >
          {debugResult && (() => {
            const isRunning = debugResult.status === 'running'
            const stepList = isRunning ? liveSteps : (debugResult.steps || [])
            return (
            <div>
              {/* 头部 — sticky 置顶，下滑不被遮挡 */}
              <div style={{
                padding: '20px 24px', display: 'flex', alignItems: 'center', gap: 12,
                position: 'sticky', top: 0, zIndex: 10,
                background: isRunning ? '#f6f3fc'
                  : passed ? '#eefaf9'
                  : '#fef1f0',
                borderBottom: '1px solid rgba(0,0,0,0.06)',
                boxShadow: '0 2px 8px rgba(0,0,0,0.04)',
              }}>
                <div style={{
                  width: 44, height: 44, borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 22,
                  background: isRunning ? '#f3f0ff' : passed ? '#e0f7f6' : '#fff2f0',
                  color: isRunning ? '#7c5cbf' : passed ? '#0ea5a0' : '#e8453c',
                }}>
                  {isRunning ? <Spin size="small" /> : passed ? <CheckCircleOutlined /> : <WarningOutlined />}
                </div>
                <div style={{ flex: 1 }}>
                  <div style={{ fontSize: 16, fontWeight: 600, color: isRunning ? '#7c5cbf' : passed ? '#0ea5a0' : '#e8453c' }}>
                    {isRunning ? '正在执行...' : passed ? '验证通过' : '验证失败'}
                  </div>
                  <div style={{ fontSize: 12, color: '#86909c', marginTop: 2 }}>
                    {isRunning
                      ? `${liveSteps.filter(s => s.status === 'passed').length} 步完成，${liveSteps.filter(s => s.status === 'running').length > 0 ? '1 步执行中' : '等待中...'}`
                      : `耗时 ${debugResult.durationMs != null ? `${(debugResult.durationMs / 1000).toFixed(1)}s` : '-'}${stepList.length > 0 ? ` · ${stepList.filter(s => s.status === 'passed').length}/${stepList.length} 步通过` : ''}`
                    }
                  </div>
                </div>
                {isRunning && (
                  <Button size="small" danger onClick={stopExecution}>停止</Button>
                )}
              </div>

              {/* 步骤时间线 */}
              {stepList.length > 0 ? (
                <div style={{ padding: '16px 24px', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#1d2129', marginBottom: 16 }}>执行过程</div>
                  {stepList.map((s, i) => {
                    const ok = s.status === 'passed'
                    const isRunning = s.status === 'running'
                    const phase = s.step_phase || s.phase
                    const name = s.step_name || s.action || s.step || `步骤 ${i + 1}`
                    const error = s.error_summary || s.error
                    const ms = s.duration_ms
                    const phaseEmoji = { setup: '🔧', action: '👆', verify: '✅' }
                    return (
                      <div key={i} style={{ display: 'flex', gap: 14 }}>
                        {/* 左：连线 */}
                        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', width: 32, flexShrink: 0 }}>
                          <div style={{
                            width: 32, height: 32, borderRadius: 16, display: 'flex', alignItems: 'center', justifyContent: 'center',
                            fontSize: 16, flexShrink: 0,
                            background: isRunning ? '#f3f0ff' : ok ? '#f0faf9' : '#fff5f5',
                            border: isRunning ? '2px solid #d3adf7' : ok ? '2px solid #b5e8e3' : '2px solid #ffccc7',
                          }}>
                            {isRunning ? <Spin size="small" /> : ok ? (phaseEmoji[phase] || '✅') : '❌'}
                          </div>
                          {i < stepList.length - 1 && (
                            <div style={{ width: 2, flex: 1, minHeight: 16, background: isRunning ? '#d3adf7' : ok ? '#b5e8e3' : '#ffccc7' }} />
                          )}
                        </div>
                        {/* 右：内容 */}
                        <div style={{ flex: 1, paddingBottom: 16, minWidth: 0 }}>
                          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minHeight: 32 }}>
                            <span style={{ fontSize: 14, fontWeight: 500, color: isRunning ? '#7c5cbf' : ok ? '#1d2129' : '#e8453c' }}>
                              {name}
                            </span>
                            <span style={{
                              fontSize: 11, padding: '1px 8px', borderRadius: 10, fontWeight: 500,
                              background: isRunning ? '#f3f0ff' : ok ? '#e0f7f6' : '#fff2f0',
                              color: isRunning ? '#7c5cbf' : ok ? '#0ea5a0' : '#e8453c',
                            }}>
                              {isRunning ? '执行中...' : ok ? '通过' : '失败'}
                            </span>
                            {ms != null && (
                              <span style={{ fontSize: 11, color: '#c9cdd4', marginLeft: 'auto' }}>
                                {ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`}
                              </span>
                            )}
                          </div>
                          {error && (
                            <div style={{
                              marginTop: 6, padding: '8px 12px', borderRadius: 8,
                              background: '#fff5f5', border: '1px solid #ffccc7',
                              fontSize: 12, color: '#e8453c', lineHeight: 1.5, wordBreak: 'break-all',
                            }}>
                              {error.substring(0, 300)}{error.length > 300 ? '...' : ''}
                            </div>
                          )}
                        </div>
                      </div>
                    )
                  })}
                </div>
              ) : debugResult.errorSummary ? (
                <div style={{ padding: '16px 24px', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#1d2129', marginBottom: 8 }}>错误信息</div>
                  <div style={{
                    padding: 12, borderRadius: 8, background: '#fff5f5', border: '1px solid #ffccc7',
                    fontSize: 12, color: '#e8453c', lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                  }}>
                    {debugResult.errorSummary}
                  </div>
                </div>
              ) : null}

              {/* 截图 */}
              {debugResult.screenshots?.length > 0 && (
                <div style={{ padding: '16px 24px', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                  <div style={{ fontSize: 13, fontWeight: 500, color: '#1d2129', marginBottom: 8 }}>失败截图</div>
                  {debugResult.screenshots.map((s, i) => (
                    <div key={i} style={{ marginBottom: 8, borderRadius: 8, overflow: 'hidden', border: '1px solid rgba(0,0,0,0.06)', cursor: 'pointer' }}
                      onClick={() => setPreviewScreenshot(s)}>
                      <img src={`data:image/png;base64,${s.base64}`} alt={s.name}
                        style={{ width: '100%', display: 'block' }} />
                    </div>
                  ))}
                </div>
              )}

              {/* 执行日志（折叠） */}
              {debugResult.stdout && (
                <details style={{ borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                  <summary style={{ padding: '12px 24px', fontSize: 13, fontWeight: 500, color: '#86909c', cursor: 'pointer' }}>
                    执行日志（调试用）
                  </summary>
                  <div style={{ padding: '0 24px 16px' }}>
                    <pre style={{
                      margin: 0, padding: 14, borderRadius: 8, fontSize: 11, lineHeight: 1.5,
                      fontFamily: "'JetBrains Mono', monospace", background: '#1e1e2e', color: '#cdd6f4',
                      maxHeight: 350, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
                    }}>
                      {debugResult.stdout}
                    </pre>
                  </div>
                </details>
              )}

              {/* 调试历史 */}
              {debugHistory.length > 0 && (
                <details>
                  <summary style={{ padding: '12px 24px', fontSize: 13, fontWeight: 500, color: '#86909c', cursor: 'pointer' }}>
                    调试历史（{debugHistory.length} 次失败记录）
                  </summary>
                  <div style={{ padding: '0 24px 16px' }}>
                    {debugHistory.map((run, i) => (
                      <div key={run.id} style={{ marginBottom: 10, padding: '8px 12px', borderRadius: 8, background: 'rgba(0,0,0,0.02)', border: '1px solid rgba(0,0,0,0.04)' }}>
                        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }}>
                          <span style={{ fontSize: 11, color: '#e8453c', fontWeight: 600 }}>#{debugHistory.length - i}</span>
                          <span style={{ fontSize: 11, color: '#86909c' }}>{run.createdAt ? new Date(run.createdAt).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : ''}</span>
                          <span style={{ fontSize: 11, color: '#c9cdd4' }}>{run.durationMs ? `${(run.durationMs / 1000).toFixed(1)}s` : ''}</span>
                        </div>
                        {run.errorSummary && (
                          <div style={{ fontSize: 11, color: '#e8453c', fontFamily: 'monospace', lineHeight: 1.4, wordBreak: 'break-all' }}>
                            {run.errorSummary.substring(0, 200)}{run.errorSummary.length > 200 ? '...' : ''}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </details>
              )}
            </div>
            )
          })()}
        </Drawer>

        {/* 截图预览弹窗 */}
        <Modal open={!!previewScreenshot} onCancel={() => setPreviewScreenshot(null)}
          footer={null} width="80%" title={previewScreenshot?.name || '截图预览'}
          styles={{ body: { padding: 0, textAlign: 'center', background: '#1e1e2e' } }}>
          {previewScreenshot && (
            <img src={`data:image/png;base64,${previewScreenshot.base64}`} alt={previewScreenshot.name}
              style={{ maxWidth: '100%', maxHeight: '80vh' }} />
          )}
        </Modal>
      </Card>
    )
  }

  // ── 接口类型：保持原有的完整 ScenarioEditor ──
  return (
    <Card styles={{ body: { padding: '16px 20px' } }}>
      {/* 顶部工具栏：视图切换 + 状态 + 模板 */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
        <Space size={8}>
          {/* 视图切换 */}
          <div style={{ display: 'inline-flex', borderRadius: 12, border: '1px solid rgba(0,0,0,0.05)', overflow: 'hidden' }}>
            <div onClick={() => setViewMode('steps')} style={{
              padding: '4px 12px', fontSize: 12, cursor: 'pointer', fontWeight: 500,
              background: viewMode === 'steps' ? accentColor : 'transparent',
              color: viewMode === 'steps' ? '#fff' : '#4e5969',
            }}>步骤视图</div>
            <div onClick={() => setViewMode('code')} style={{
              padding: '4px 12px', fontSize: 12, cursor: 'pointer', fontWeight: 500,
              background: viewMode === 'code' ? '#1e1e1e' : 'transparent',
              color: viewMode === 'code' ? '#d4d4d4' : '#4e5969',
              borderLeft: '1px solid rgba(0,0,0,0.06)',
            }}>代码视图</div>
          </div>
          <Select size="small" value={scenarioStatus} onChange={setScenarioStatus} style={{ width: 100 }}
            options={Object.entries(scenarioStatusMap).map(([k, v]) => ({
              value: k, label: <span style={{ color: v.color }}>{v.label}</span>
            }))} />
          <Tooltip title={scenarioStatus === 'completed' ? (isTemplate ? '取消模板' : '标记为模板') : '仅已完成可标记'}>
            <Button size="small" type={isTemplate ? 'primary' : 'default'}
              disabled={scenarioStatus !== 'completed'}
              icon={isTemplate ? <StarFilled /> : <StarOutlined />}
              onClick={() => setIsTemplate(!isTemplate)}
              style={isTemplate ? { background: '#fff7e6', borderColor: '#ffc069', color: '#fa8c16' } : {}}>
              {isTemplate ? '模板' : '标记模板'}
            </Button>
          </Tooltip>
        </Space>
        <Space>
          <Select size="small" value={runEnv} onChange={onEnvChange} style={{ width: 160 }}
            popupMatchSelectWidth={false}
            placeholder="选择环境" options={buildEnvOptions(environments)} />
          <Button size="small" type="primary" icon={<PlayCircleOutlined />}
            loading={debugRunning} disabled={!runEnv}
            onClick={handleDebugRun}
            style={{ background: accentColor, borderColor: accentColor }}>
            调试运行
          </Button>
          <Button size="small" icon={<ImportOutlined />} onClick={onImportTemplate}>从模板导入</Button>
          <Button size="small" danger type="text" onClick={() => {
            Modal.confirm({
              title: '确认删除场景', content: '删除后场景数据将清空，确定继续？',
              onOk: () => { setScenario(null); setScenarioStatus('draft'); setIsTemplate(false) },
            })
          }}><DeleteOutlined /> 删除</Button>
        </Space>
      </div>

      {/* 步骤视图 */}
      {viewMode === 'steps' && (
        <>
          {type === 'api' ? (
            <ApiStepList steps={steps} onChange={newSteps => updateScenario({ steps: newSteps })} accentColor={accentColor}
                environments={environments} runEnv={runEnv} onEnvChange={setRunEnv} />
          ) : (
            <div style={{ marginBottom: 12 }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                <h4 style={{ fontSize: 13, color: '#86909c', margin: 0 }}>UI 测试步骤</h4>
                <Button type="primary" ghost size="small" icon={<PlusOutlined />} onClick={addStep}>添加步骤</Button>
              </div>
              <div style={{ borderRadius: 12, border: '1px solid rgba(0,0,0,0.04)', overflow: 'hidden' }}>
                <div style={{
                  display: 'flex', gap: 6, padding: '6px 14px', fontSize: 12, fontWeight: 600,
                  background: 'rgba(0,0,0,0.02)', color: '#86909c', borderBottom: '1px solid rgba(0,0,0,0.04)', alignItems: 'center',
                }}>
                  <span style={{ width: 24, flexShrink: 0 }}></span>
                  <span style={{ width: 28, flexShrink: 0 }}>#</span>
                  <span style={{ flex: 2 }}>操作步骤</span>
                  <span style={{ flex: 1 }}>页面/元素</span>
                  <span style={{ flex: 1 }}>预期结果</span>
                  <span style={{ width: 32, flexShrink: 0 }}></span>
                </div>
                {steps.map((s, i) => (
                  <div key={i} style={{
                    display: 'flex', gap: 6, padding: '6px 14px', fontSize: 13,
                    background: i % 2 === 0 ? 'transparent' : 'rgba(0,0,0,0.015)',
                    borderBottom: i < steps.length - 1 ? '1px solid rgba(0,0,0,0.03)' : 'none', alignItems: 'center',
                  }}>
                    <HolderOutlined style={{ color: 'rgba(0,0,0,0.15)', cursor: 'grab', flexShrink: 0 }} />
                    <span style={{
                      width: 28, height: 24, borderRadius: 12, background: '#e0f7f6', color: '#0ea5a0',
                      display: 'flex', alignItems: 'center', justifyContent: 'center', fontWeight: 600, fontSize: 12, flexShrink: 0,
                    }}>{s.seq}</span>
                    <Input value={s.action || ''} onChange={e => updateStepField(i, 'action', e.target.value)}
                      placeholder="描述操作步骤..." variant="borderless" style={{ flex: 2, fontSize: 13 }} />
                    <Input value={s.uiTarget || ''} onChange={e => updateStepField(i, 'uiTarget', e.target.value)}
                      placeholder="页面URL或元素选择器" variant="borderless"
                      style={{ flex: 1, fontSize: 12, fontFamily: 'monospace', color: accentColor }} />
                    <Input value={s.expected || ''} onChange={e => updateStepField(i, 'expected', e.target.value)}
                      placeholder="预期结果..." variant="borderless" style={{ flex: 1, fontSize: 13, color: '#86909c' }} />
                    <Button type="text" danger size="small" icon={<DeleteOutlined />}
                      onClick={() => removeStep(i)} disabled={steps.length <= 1}
                      style={{ flexShrink: 0, opacity: steps.length <= 1 ? 0.3 : 1 }} />
                  </div>
                ))}
              </div>
              <Button type="dashed" block style={{ marginTop: 8, borderRadius: 12 }} icon={<PlusOutlined />} onClick={addStep}>添加步骤</Button>
            </div>
          )}
        </>
      )}

      {/* 代码视图 — 内嵌 ScriptEditor */}
      {viewMode === 'code' && (
        <div>
          <div style={{ fontSize: 12, color: '#86909c', marginBottom: 8 }}>
            基于步骤自动生成的可执行代码，也可以直接编辑。保存后可点击「运行」执行。
          </div>
          <ScriptEditor
            projectId={projectId} branchId={branchId} caseId={caseId}
            scriptType={type === 'api' ? 'api' : 'ui'} accentColor={accentColor}
            autoGenerateCode={type === 'api' ? generateApiCodeFromSteps(steps, caseTitle, (() => { const env = runEnv && environments?.find(e => e.id === runEnv); return env?.variables?.find(v => v.key === 'BASE_URL')?.value || '' })()) : generateUiCode(steps, caseTitle)}
            onScriptSaved={onScriptSaved}
            envId={runEnv}
          />
        </div>
      )}

      {/* 调试运行结果（内联） */}
      {debugResult && (
        <div style={{ marginTop: 16, border: `1px solid ${debugResult.status === 'passed' ? 'rgba(14,165,160,0.3)' : 'rgba(232,69,60,0.3)'}`, borderRadius: 12, overflow: 'hidden' }}>
          <div style={{
            padding: '8px 14px', display: 'flex', alignItems: 'center', gap: 10,
            background: debugResult.status === 'passed' ? 'rgba(14,165,160,0.06)' : 'rgba(232,69,60,0.06)',
          }}>
            <Tag color={debugResult.status === 'passed' ? 'cyan' : 'error'} style={{ margin: 0, fontWeight: 600 }}>
              {(debugResult.status || 'UNKNOWN').toUpperCase()}
            </Tag>
            {debugResult.durationMs != null && <span style={{ fontSize: 12, color: '#86909c' }}>{(debugResult.durationMs / 1000).toFixed(1)}s</span>}
            <div style={{ flex: 1 }} />
            <Button type="text" size="small" onClick={() => setDebugResult(null)} style={{ color: '#c9cdd4' }}>关闭</Button>
          </div>
          {debugResult.errorSummary && (
            <div style={{ padding: '8px 14px', fontSize: 12, color: '#e8453c', fontFamily: 'monospace' }}>{debugResult.errorSummary}</div>
          )}
          {debugResult.stdout && (
            <pre style={{ margin: 0, padding: 14, fontSize: 11, fontFamily: 'monospace', background: '#1e1e2e', color: '#cdd6f4', maxHeight: 300, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
              {debugResult.stdout}
            </pre>
          )}
          {debugResult.screenshots?.length > 0 && (
            <div style={{ padding: '10px 14px', borderTop: '1px solid rgba(0,0,0,0.04)' }}>
              <div style={{ fontSize: 12, color: '#86909c', marginBottom: 8 }}>失败截图 ({debugResult.screenshots.length})</div>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                {debugResult.screenshots.map((s, i) => (
                  <div key={i} style={{ cursor: 'pointer', borderRadius: 8, overflow: 'hidden', border: '1px solid rgba(0,0,0,0.06)' }}
                    onClick={() => setPreviewScreenshot(s)}>
                    <img src={`data:image/png;base64,${s.base64}`} alt={s.name}
                      style={{ width: 160, height: 100, objectFit: 'cover', display: 'block' }} />
                    <div style={{ fontSize: 11, color: '#86909c', padding: '2px 6px', background: 'rgba(0,0,0,0.02)' }}>{s.name}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

    </Card>
  )
}

function TemplateModal({ open, onClose, projectId, branchId, scenarioType, onSelect }) {
  const [templates, setTemplates] = useState([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (open && projectId && branchId) {
      setLoading(true)
      api.get(`/projects/${projectId}/branches/${branchId}/cases/templates?type=${scenarioType}`)
        .then(res => setTemplates(res.data || []))
        .catch(() => message.error('加载模板失败'))
        .finally(() => setLoading(false))
    }
  }, [open, projectId, branchId, scenarioType])

  const scenario = scenarioType === 'api' ? 'apiScenario' : 'uiScenario'

  return (
    <Modal title={`从模板导入 — ${scenarioType === 'api' ? '接口' : 'UI'}测试场景`}
      open={open} onCancel={onClose} footer={null} width={640}>
      {loading ? <div style={{ textAlign: 'center', padding: 32 }}><Spin /></div> : (
        templates.length === 0 ? (
          <Empty description="暂无模板" image={Empty.PRESENTED_IMAGE_SIMPLE}>
            <div style={{ fontSize: 12, color: '#86909c' }}>将已完成的场景标记为模板后即可在此引用</div>
          </Empty>
        ) : (
          <div style={{ maxHeight: 400, overflow: 'auto' }}>
            {templates.map(t => {
              const sc = t[scenario]
              return (
                <div key={t.id} style={{
                  padding: '12px 16px', borderRadius: 12, border: '1px solid rgba(0,0,0,0.04)',
                  marginBottom: 8, cursor: 'pointer', transition: 'all 0.15s',
                }}
                  onMouseEnter={e => e.currentTarget.style.borderColor = 'rgba(14,165,160,0.3)'}
                  onMouseLeave={e => e.currentTarget.style.borderColor = 'rgba(0,0,0,0.04)'}
                  onClick={() => { onSelect(sc); onClose() }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
                    <span style={{ fontWeight: 500, fontSize: 14 }}>
                      <StarFilled style={{ color: '#faad14', marginRight: 6 }} />
                      {t.title}
                    </span>
                    <Tag color="blue" style={{ fontSize: 11 }}>{sc?.steps?.length || 0} 步</Tag>
                  </div>
                  <div style={{ fontSize: 12, color: '#86909c' }}>
                    <span style={{ fontFamily: 'monospace' }}>{t.caseCode}</span>
                    {sc?.scriptRefFile && <span style={{ marginLeft: 8, fontFamily: 'monospace' }}>{sc.scriptRefFile}</span>}
                  </div>
                </div>
              )
            })}
          </div>
        )
      )}
    </Modal>
  )
}

export default function CaseDetail() {
  const { projectId, caseId } = useParams()
  const navigate = useNavigate()

  const searchParams = new URLSearchParams(window.location.search)
  const branchId = searchParams.get('branchId')

  const [loading, setLoading] = useState(true)
  const [caseData, setCaseData] = useState(null)
  const [environments, setEnvironments] = useState([])
  const [folders, setFolders] = useState([])

  const [runModalOpen, setRunModalOpen] = useState(false)
  const [runStatus, setRunStatus] = useState('idle')
  const [runResult, setRunResult] = useState(null)
  const [runEnv, setRunEnv] = useEnv(projectId)
  const [hasActiveScript, setHasActiveScript] = useState(false)
  const [linkedApiCount, setLinkedApiCount] = useState(0)   // 回推的编排接口场景条数
  const [scriptRuns, setScriptRuns] = useState([])
  const [scriptRunsLoading, setScriptRunsLoading] = useState(false)
  const [activeTab, setActiveTab] = useState('manual')

  // 编辑状态
  const [title, setTitle] = useState('')
  const [type, setType] = useState('api')
  const [priority, setPriority] = useState('P1')
  const [module, setModule] = useState('')
  const [subModule, setSubModule] = useState('')
  const [automationStatus, setAutomationStatus] = useState('pending')
  const [lifecycleStatus, setLifecycleStatus] = useState('draft')
  const [manualStatus, setManualStatus] = useState('not_started')
  const [uiStatus, setUiStatus] = useState('not_started')
  const [apiStatus, setApiStatus] = useState('not_started')
  const [isCore, setIsCore] = useState(false)
  const [flaky, setFlaky] = useState(false)
  const [preconditions, setPreconditions] = useState('')
  const [expectedResult, setExpectedResult] = useState('')
  const [scriptRefFile, setScriptRefFile] = useState('')
  const [scriptRefFunc, setScriptRefFunc] = useState('')
  const [remark, setRemark] = useState('')
  const [steps, setSteps] = useState([{ seq: 1, action: '', expected: '' }])
  const [variablesUsed, setVariablesUsed] = useState([])
  const [newVarInput, setNewVarInput] = useState('')
  const [apiScenario, setApiScenario] = useState(null)
  const [uiScenario, setUiScenario] = useState(null)
  const [apiScenarioStatus, setApiScenarioStatus] = useState('draft')
  const [uiScenarioStatus, setUiScenarioStatus] = useState('draft')
  const [isApiTemplate, setIsApiTemplate] = useState(false)
  const [isUiTemplate, setIsUiTemplate] = useState(false)

  // 模板弹窗
  const [templateModalOpen, setTemplateModalOpen] = useState(false)
  const [templateModalType, setTemplateModalType] = useState('api')
  const [svDrawerOpen, setSvDrawerOpen] = useState(false)

  // 脚本查看
  const [scriptContent, setScriptContent] = useState(null)
  const [scriptLoading, setScriptLoading] = useState(false)
  const [scriptError, setScriptError] = useState(null)

  const savedRef = useRef('')

  useEffect(() => {
    if (branchId) loadData()
  }, [projectId, branchId, caseId])

  useEffect(() => {
    if (!runEnv) return
    const env = environments.find(e => e.id === runEnv)
    if (env && !env.variables) {
      api.get(`/environments/${runEnv}/variables`).then(res => {
        env.variables = res.data || []
        setEnvironments([...environments])
      }).catch(() => {})
    }
  }, [runEnv])

  async function loadData() {
    if (!branchId) { message.error('缺少分支信息'); setLoading(false); return }
    setLoading(true)
    try {
      const [caseRes, envRes, folderRes] = await Promise.all([
        api.get(`/projects/${projectId}/branches/${branchId}/cases/${caseId}`),
        api.get('/environments'),
        api.get(`/projects/${projectId}/branches/${branchId}/folders`),
      ])
      const c = caseRes.data
      setCaseData(c)

      const allFolders = folderRes.data || []
      setFolders(allFolders)
      const folderPath = c.folderId ? findFolderPath(allFolders, c.folderId) : ''
      let newModule = '', newSubModule = ''
      if (folderPath) {
        const parts = folderPath.split('/')
        newModule = parts.slice(0, -1).join('/') || parts[0] || ''
        newSubModule = parts.length > 1 ? parts[parts.length - 1] : ''
      }

      const vals = {
        title: c.title || '', type: c.type || 'api', priority: c.priority || 'P1',
        module: newModule, subModule: newSubModule,
        automationStatus: c.automationStatus || 'pending', flaky: c.isFlaky || false,
        preconditions: c.preconditions || '', expectedResult: c.expectedResult || '',
        scriptRefFile: c.scriptRefFile || '', scriptRefFunc: c.scriptRefFunc || '',
        remark: c.remark || '',
        steps: c.steps?.length ? c.steps.map((s, i) => ({ ...s, seq: s.seq || i + 1 })) : [{ seq: 1, action: '', expected: '' }],
        variablesUsed: c.variablesUsed || [],
        apiScenario: c.apiScenario || null,
        uiScenario: c.uiScenario || null,
        apiScenarioStatus: c.apiScenarioStatus || 'draft',
        uiScenarioStatus: c.uiScenarioStatus || 'draft',
        isApiTemplate: c.isApiTemplate || false,
        isUiTemplate: c.isUiTemplate || false,
        lifecycleStatus: c.lifecycleStatus || 'draft',
        manualStatus: c.manualStatus || 'not_started',
        uiStatus: c.uiStatus || 'not_started',
        apiStatus: c.apiStatus || 'not_started',
        isCore: c.isCore || false,
      }

      setTitle(vals.title); setType(vals.type); setPriority(vals.priority)
      setModule(vals.module); setSubModule(vals.subModule)
      setAutomationStatus(vals.automationStatus); setFlaky(vals.flaky)
      setLifecycleStatus(vals.lifecycleStatus); setManualStatus(vals.manualStatus)
      setUiStatus(vals.uiStatus); setApiStatus(vals.apiStatus)
      setIsCore(vals.isCore)
      setPreconditions(vals.preconditions); setExpectedResult(vals.expectedResult)
      setScriptRefFile(vals.scriptRefFile); setScriptRefFunc(vals.scriptRefFunc)
      setRemark(vals.remark); setSteps(vals.steps); setVariablesUsed(vals.variablesUsed)
      setApiScenario(vals.apiScenario); setUiScenario(vals.uiScenario)
      setApiScenarioStatus(vals.apiScenarioStatus); setUiScenarioStatus(vals.uiScenarioStatus)
      setIsApiTemplate(vals.isApiTemplate); setIsUiTemplate(vals.isUiTemplate)

      savedRef.current = JSON.stringify(vals)

      // Check if there's an active script in the scripts table
      try {
        const scriptRes = await api.get(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/active?type=${vals.type}`)
        setHasActiveScript(!!scriptRes.data)
      } catch { setHasActiveScript(false) }

      const envs = envRes.data || []
      setEnvironments(envs)
      // 如果 useEnv 还没持久化选择，默认选第一个
      if (envs.length && !runEnv) {
        setRunEnv(envs[0].id)
      }
      // 加载已选环境的变量
      const activeEnvId = runEnv || (envs.length ? envs[0].id : null)
      if (activeEnvId) {
        const env = envs.find(e => e.id === activeEnvId)
        if (env) {
          try {
            const varRes = await api.get(`/environments/${activeEnvId}/variables`)
            env.variables = varRes.data || []
            setEnvironments([...envs])
          } catch {}
        }
      }
    } catch { message.error('加载用例详情失败') }
    finally { setLoading(false) }
  }

  const currentSnap = JSON.stringify({
    title, type, priority, module, subModule, automationStatus, flaky,
    preconditions, expectedResult, scriptRefFile, scriptRefFunc, remark,
    steps, variablesUsed, apiScenario, uiScenario,
    apiScenarioStatus, uiScenarioStatus, isApiTemplate, isUiTemplate,
    lifecycleStatus, manualStatus, uiStatus, apiStatus, isCore,
  })
  const isDirty = caseData && currentSnap !== savedRef.current

  async function loadScript() {
    if (!branchId || !scriptRefFile) return
    setScriptLoading(true); setScriptError(null)
    try {
      const res = await api.get(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/script`)
      setScriptContent(res.data)
    } catch (err) {
      setScriptError(err?.response?.data?.message || '加载脚本失败')
      setScriptContent(null)
    } finally { setScriptLoading(false) }
  }

  async function loadScriptRuns() {
    setScriptRunsLoading(true)
    try {
      const res = await api.get(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/runs?type=${type}`)
      setScriptRuns(res.data || [])
    } catch { setScriptRuns([]) }
    finally { setScriptRunsLoading(false) }
  }

  useEffect(() => {
    const handler = (e) => { if (isDirty) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [isDirty])

  const handleBack = () => {
    if (isDirty) {
      Modal.confirm({
        title: '未保存的修改', content: '当前有未保存的修改，确定离开吗？',
        okText: '离开', cancelText: '继续编辑', onOk: () => navigate(-1),
      })
    } else navigate(-1)
  }

  const addStep = () => setSteps(prev => [...prev, { seq: prev.length + 1, action: '', expected: '' }])
  const removeStep = (idx) => setSteps(prev => prev.filter((_, i) => i !== idx).map((s, i) => ({ ...s, seq: i + 1 })))
  const updateStep = (idx, field, value) => setSteps(prev => prev.map((s, i) => i === idx ? { ...s, [field]: value } : s))

  const handleSave = async () => {
    try {
      await api.put(`/projects/${projectId}/branches/${branchId}/cases/${caseId}`, {
        title, type, priority, module, subModule, automationStatus,
        isFlaky: flaky, preconditions, expectedResult, scriptRefFile, scriptRefFunc,
        remark, steps, variablesUsed, apiScenario, uiScenario,
        apiScenarioStatus, uiScenarioStatus, isApiTemplate, isUiTemplate,
        lifecycleStatus, manualStatus, uiStatus, apiStatus, isCore,
      })
      savedRef.current = currentSnap
      setCaseData(prev => ({ ...prev }))
      message.success('保存成功')
    } catch { message.error('保存失败') }
  }

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin size="large" /></div>
  if (!caseData) return <div style={{ textAlign: 'center', padding: 80, color: '#86909c' }}>用例不存在</div>

  const caseCode = caseData.caseCode || caseData.id?.substring(0, 8)
  const hasApi = !!apiScenario
  const hasUi = !!uiScenario

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 16 }}>
        <Button type="text" icon={<ArrowLeftOutlined />} size="small" onClick={handleBack} style={{ color: '#86909c' }} />
        <span style={{ fontSize: 12, color: '#c9cdd4' }}>用例管理</span>
        <span style={{ color: 'rgba(0,0,0,0.15)', fontSize: 12 }}>/</span>
        <span style={{ fontSize: 12, color: '#86909c', fontFamily: 'monospace' }}>{caseCode}</span>
      </div>

      <Card styles={{ body: { padding: '16px 20px' } }} style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
          <Button type="primary" size="small" icon={<SaveOutlined />} disabled={!isDirty} onClick={handleSave}>保存</Button>
          <Input value={title} onChange={e => setTitle(e.target.value)} variant="borderless"
            style={{ fontSize: 16, fontWeight: 600, flex: 1, padding: '2px 4px' }} />
          <Tooltip title="场景变量（UI 与接口测试共用，${变量名} 引用，random 执行时唯一化）">
            <Button size="small" icon={<DatabaseOutlined />} onClick={() => setSvDrawerOpen(true)}>场景变量</Button>
          </Tooltip>
          <Tooltip title={isCore ? '取消核心（标杆用例，供其他用例参考应用它来生成）' : '设为核心（标杆用例，供其他用例参考应用它来生成）'}>
            <Button size="small" type={isCore ? 'primary' : 'default'}
              icon={isCore ? <StarFilled /> : <StarOutlined />}
              onClick={() => setIsCore(v => !v)}
              style={isCore ? { background: '#fff7e6', borderColor: '#ffc069', color: '#fa8c16' } : {}}>
              {isCore ? '核心' : '设为核心'}
            </Button>
          </Tooltip>
        </div>

        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, alignItems: 'center' }}>
          <ReadonlyProp icon={<TagOutlined />} value={caseCode} />
          <InlineProp icon={<FlagOutlined />} value={priority} color={priorityColors[priority]} bg={priorityBg[priority]}>
            <DropdownList activeKey={priority} onSelect={setPriority}
              items={['P0','P1','P2','P3'].map(p => ({ key: p, label: p, dot: 'square', color: dotColors[p] }))} />
          </InlineProp>
          <InlineProp icon={<ApiOutlined />} value={type?.toUpperCase()} color={type==='api'?'#0ea5a0':'#0ea5a0'} bg={type==='api'?'#e0f7f6':'#e0f7f6'}>
            <DropdownList activeKey={type} onSelect={setType} items={['api','e2e'].map(t => ({ key: t, label: t.toUpperCase() }))} />
          </InlineProp>
          <ReadonlyProp icon={<AppstoreOutlined />} label="模块" value={[module, subModule].filter(Boolean).join(' / ') || '未分类'} />
          <InlineProp icon={<ThunderboltOutlined />} value={lifecycleMap[lifecycleStatus]?.label || lifecycleStatus}
            color={lifecycleMap[lifecycleStatus]?.color} bg={lifecycleMap[lifecycleStatus]?.bg}>
            <DropdownList activeKey={lifecycleStatus} onSelect={setLifecycleStatus}
              items={['draft','done','deprecated'].map(s => ({ key: s, label: lifecycleMap[s].label, dot: 'circle', color: lifecycleMap[s].color }))} />
          </InlineProp>
          {/* 三维执行就绪度（手动/UI/接口 统一状态，可编辑）——批量执行按此判断能否跑 */}
          {[['手动', manualStatus, setManualStatus], ['UI', uiStatus, setUiStatus], ['接口', apiStatus, setApiStatus]].map(([lbl, val, setter]) => (
            <InlineProp key={lbl} value={`${lbl}·${dimStatusMap[val]?.label || val}`}
              color={dimStatusMap[val]?.color} bg={dimStatusMap[val]?.bg}>
              <DropdownList activeKey={val} onSelect={setter}
                items={DIM_STATUS_KEYS.map(s => ({ key: s, label: dimStatusMap[s].label, dot: 'circle', color: dimStatusMap[s].color }))} />
            </InlineProp>
          ))}
          <InlineProp icon={<WarningOutlined />} value={flaky ? 'Flaky' : '正常'} color={flaky ? '#faad14' : '#86909c'} bg={flaky ? '#fffbe6' : 'rgba(0,0,0,0.02)'}>
            <div style={{ padding: '4px 8px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
              <span style={{ fontSize: 13 }}>Flaky 标记</span>
              <Switch size="small" checked={flaky} onChange={v => setFlaky(v)} />
            </div>
          </InlineProp>
          <ReadonlyProp label="来源" value={caseData.source || 'manual'} />
          {caseData.reviewStatus && (
            <ReadonlyProp label="审核" value={
              caseData.reviewStatus === 'approved' ? '✓ 已审核' :
              caseData.reviewStatus === 'rejected' ? '✕ 已拒绝' : '◐ 待审核'
            } />
          )}
          {caseData.qualityScore?.total != null && (
            <ReadonlyProp label="评分" value={caseData.qualityScore.total} />
          )}

          {/* 场景覆盖指示器 — 显示状态 + 模板 */}
          <div style={{ display: 'inline-flex', gap: 4, marginLeft: 8 }}>
            <Tooltip title="手动测试步骤">
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 3, padding: '2px 8px',
                borderRadius: 12, fontSize: 11, fontWeight: 500,
                background: '#e0f7f6', color: '#0ea5a0', border: '1px solid rgba(14,165,160,0.2)',
              }}><CheckCircleOutlined style={{ fontSize: 10 }} /> 手动 ({steps.length}步)</span>
            </Tooltip>
            <Tooltip title={hasApi ? `接口场景 · ${(scenarioStatusMap[apiScenarioStatus] || {}).label || '草稿'}${isApiTemplate ? ' · 模板' : ''}` : '暂无接口测试场景，点击接口测试 Tab 创建'}>
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 3, padding: '2px 8px',
                borderRadius: 12, fontSize: 11, fontWeight: 500,
                background: hasApi ? '#e0f7f6' : 'rgba(0,0,0,0.02)',
                color: hasApi ? (scenarioStatusMap[apiScenarioStatus] || {}).color || '#0ea5a0' : '#c9cdd4',
                border: `1px solid ${hasApi ? 'rgba(14,165,160,0.2)' : 'rgba(0,0,0,0.06)'}`,
              }}>
                {isApiTemplate && <StarFilled style={{ fontSize: 9, color: '#faad14' }} />}
                <ApiOutlined style={{ fontSize: 10 }} /> API
                {hasApi && <span>({apiScenario?.steps?.length || 0}步 · {(scenarioStatusMap[apiScenarioStatus] || {}).label || '草稿'})</span>}
              </span>
            </Tooltip>
            <Tooltip title={hasUi ? `UI 场景 · ${(scenarioStatusMap[uiScenarioStatus] || {}).label || '草稿'}${isUiTemplate ? ' · 模板' : ''} · 括号内为脚本逻辑步骤数（≠ 实际执行轨迹步数）` : '暂无 UI 测试场景，点击 UI 测试 Tab 创建'}>
              <span style={{
                display: 'inline-flex', alignItems: 'center', gap: 3, padding: '2px 8px',
                borderRadius: 12, fontSize: 11, fontWeight: 500,
                background: hasUi ? '#f5f0ff' : 'rgba(0,0,0,0.02)',
                color: hasUi ? (scenarioStatusMap[uiScenarioStatus] || {}).color || '#7c5cbf' : '#c9cdd4',
                border: `1px solid ${hasUi ? 'rgba(124,92,191,0.3)' : 'rgba(0,0,0,0.06)'}`,
              }}>
                {isUiTemplate && <StarFilled style={{ fontSize: 9, color: '#faad14' }} />}
                <DesktopOutlined style={{ fontSize: 10 }} /> UI
                {hasUi && <span>({(uiScenario?.steps?.length || uiScenario?.lastResults?.length || 0)}步 · {(scenarioStatusMap[uiScenarioStatus] || {}).label || '草稿'})</span>}
              </span>
            </Tooltip>
          </div>
        </div>
      </Card>

      <div style={{ display: 'flex', gap: 16 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <Tabs activeKey={activeTab} onChange={k => { setActiveTab(k); if (k === 'history') loadScriptRuns() }} items={[
            { key: 'manual', label: '手动测试步骤', children: (
              <Card styles={{ body: { padding: '16px 20px' } }}>
                <div style={{ marginBottom: 20 }}>
                  <h4 style={{ fontSize: 13, color: '#86909c', marginBottom: 8 }}>前置条件</h4>
                  <Input.TextArea rows={2} value={preconditions} onChange={e => setPreconditions(e.target.value)}
                    style={{ background: 'rgba(0,0,0,0.02)', borderColor: 'rgba(0,0,0,0.04)' }} autoSize={{ minRows: 2, maxRows: 6 }} />
                </div>

                <div style={{ marginBottom: 20 }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                    <h4 style={{ fontSize: 13, color: '#86909c', margin: 0 }}>测试步骤</h4>
                    <Button type="primary" ghost size="small" icon={<PlusOutlined />} onClick={addStep}>添加步骤</Button>
                  </div>
                  <StepTable steps={steps} updateStep={updateStep} addStep={addStep} removeStep={removeStep} />
                  <Button type="dashed" block style={{ marginTop: 8, borderRadius: 12 }} icon={<PlusOutlined />} onClick={addStep}>添加步骤</Button>
                </div>

                <div style={{ marginBottom: 20 }}>
                  <h4 style={{ fontSize: 13, color: '#86909c', marginBottom: 8 }}>预期结果</h4>
                  <Input.TextArea value={expectedResult} onChange={e => setExpectedResult(e.target.value)}
                    style={{ background: 'rgba(0,0,0,0.02)', borderColor: 'rgba(0,0,0,0.04)' }} autoSize={{ minRows: 2, maxRows: 6 }} />
                </div>

                <div>
                  <h4 style={{ fontSize: 13, color: '#86909c', marginBottom: 8 }}>备注</h4>
                  <Input.TextArea value={remark} onChange={e => setRemark(e.target.value)}
                    placeholder="可选备注信息" style={{ background: 'rgba(0,0,0,0.02)', borderColor: 'rgba(0,0,0,0.04)' }}
                    autoSize={{ minRows: 2, maxRows: 4 }} />
                </div>

                <div style={{ marginTop: 20 }}>
                  <h4 style={{ fontSize: 13, color: '#86909c', marginBottom: 8 }}>依赖参数</h4>
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                    {variablesUsed.map((v, i) => (
                      <Tag key={i} closable onClose={() => setVariablesUsed(prev => prev.filter((_, j) => j !== i))}
                        style={{ fontFamily: 'monospace', fontSize: 11, background: '#edf3ff', border: '1px solid rgba(78,138,240,0.3)', color: '#4e8af0', borderRadius: 12, padding: '1px 6px' }}>
                        {v}
                      </Tag>
                    ))}
                    {variablesUsed.length === 0 && <span style={{ fontSize: 12, color: '#c9cdd4' }}>暂无</span>}
                  </div>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <Input value={newVarInput} onChange={e => setNewVarInput(e.target.value)} size="small"
                      placeholder="参数名" style={{ flex: 1, fontFamily: 'monospace', fontSize: 11 }}
                      onKeyDown={e => { if (e.key === 'Enter' && newVarInput.trim()) { setVariablesUsed(prev => [...prev, newVarInput.trim()]); setNewVarInput('') } }} />
                    <Button size="small" icon={<PlusOutlined />} disabled={!newVarInput.trim()}
                      onClick={() => { setVariablesUsed(prev => [...prev, newVarInput.trim()]); setNewVarInput('') }} />
                  </div>
                </div>
              </Card>
            )},

            { key: 'api', label: <span><ApiOutlined style={{ marginRight: 4, color: hasApi ? '#0ea5a0' : undefined }} />接口测试{hasApi && <span style={{ fontSize: 11, color: '#0ea5a0', marginLeft: 4 }}>({apiScenario?.steps?.length || 0}步)</span>}</span>, children: (
              <>
                <LinkedApiScenarios
                  projectId={projectId} branchId={branchId} caseId={caseId}
                  active={activeTab === 'api'} caseTitle={title}
                  environments={environments} runEnv={runEnv} onEnvChange={setRunEnv}
                  onCountChange={setLinkedApiCount}
                />
                {/* 内嵌场景(cases.api_scenario)是历史遗留的第二份存储。新建一律走上面的
                    统一入口，这里只在该用例确实还有旧数据时显示，避免它们看不见也删不掉。 */}
                {hasApi && (
                  <ScenarioEditor
                    scenario={apiScenario} setScenario={setApiScenario}
                    scenarioStatus={apiScenarioStatus} setScenarioStatus={setApiScenarioStatus}
                    isTemplate={isApiTemplate} setIsTemplate={setIsApiTemplate}
                    type="api" accentColor="#0ea5a0"
                    onImportTemplate={() => { setTemplateModalType('api'); setTemplateModalOpen(true) }}
                    manualSteps={steps} caseTitle={title}
                    projectId={projectId} branchId={branchId} caseId={caseId}
                    environments={environments} runEnv={runEnv} onEnvChange={setRunEnv}
                    onScriptSaved={() => setHasActiveScript(true)}
                    linkedCount={linkedApiCount}
                  />
                )}
              </>
            )},

            { key: 'ui', label: <span><DesktopOutlined style={{ marginRight: 4, color: hasUi ? '#7c5cbf' : undefined }} />UI 测试{hasUi && <Tooltip title="脚本逻辑步骤数（源自手动步骤）。实际执行步数见「执行轨迹」，两者通常不同"><span style={{ fontSize: 11, color: '#7c5cbf', marginLeft: 4, borderBottom: '1px dotted #7c5cbf' }}>({(uiScenario?.steps?.length || uiScenario?.lastResults?.length || 0)}步)</span></Tooltip>}</span>, children: (
              <ScenarioEditor
                scenario={uiScenario} setScenario={setUiScenario}
                scenarioStatus={uiScenarioStatus} setScenarioStatus={setUiScenarioStatus}
                isTemplate={isUiTemplate} setIsTemplate={setIsUiTemplate}
                type="e2e" accentColor="#7c5cbf"
                onImportTemplate={() => { setTemplateModalType('ui'); setTemplateModalOpen(true) }}
                manualSteps={steps} caseTitle={title}
                projectId={projectId} branchId={branchId} caseId={caseId}
                environments={environments} runEnv={runEnv} onEnvChange={setRunEnv}
                onScriptSaved={() => setHasActiveScript(true)}
              />
            )},

            { key: 'history', label: '执行历史', children: (
              <Card styles={{ body: { padding: '16px 24px' } }}>
                <Table
                  size="small"
                  loading={scriptRunsLoading}
                  dataSource={scriptRuns}
                  rowKey="id"
                  pagination={false}
                  locale={{ emptyText: '暂无执行记录' }}
                  expandable={{
                    expandedRowRender: r => (
                      <div>
                        {r.stdout ? (
                          <pre style={{ margin: 0, padding: 12, background: '#1e1e1e', color: '#d4d4d4', borderRadius: 12, fontSize: 12, fontFamily: 'monospace', maxHeight: 300, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{r.stdout}</pre>
                        ) : <span style={{ color: '#c9cdd4' }}>无输出日志</span>}
                        {r.screenshots?.length > 0 && (
                          <div style={{ marginTop: 8, display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                            {r.screenshots.map((s, i) => (
                              <div key={i} style={{ borderRadius: 8, overflow: 'hidden', border: '1px solid rgba(0,0,0,0.06)', cursor: 'pointer' }}
                                onClick={() => window.open(`data:image/png;base64,${s.base64}`, '_blank')}>
                                <img src={`data:image/png;base64,${s.base64}`} alt={s.name}
                                  style={{ width: 120, height: 75, objectFit: 'cover', display: 'block' }} />
                                <div style={{ fontSize: 10, color: '#86909c', padding: '1px 4px', background: 'rgba(0,0,0,0.02)' }}>{s.name}</div>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    ),
                    rowExpandable: () => true,
                  }}
                  columns={[
                    {
                      title: '时间', dataIndex: 'createdAt', width: 170,
                      render: v => v ? new Date(v).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '-'
                    },
                    {
                      title: '状态', dataIndex: 'status', width: 100,
                      render: v => <Tag color={v === 'passed' ? undefined : v === 'failed' ? 'error' : 'warning'} style={{ fontWeight: 600, ...(v === 'passed' ? { background: '#e0f7f6', color: '#0ea5a0', border: 'none' } : {}) }}>{(v || 'unknown').toUpperCase()}</Tag>
                    },
                    {
                      title: '耗时', dataIndex: 'durationMs', width: 100,
                      render: v => v != null ? `${(v / 1000).toFixed(1)}s` : '-'
                    },
                    {
                      title: '错误摘要', dataIndex: 'errorSummary', ellipsis: true,
                      render: v => v ? <span style={{ color: '#e8453c', fontFamily: 'monospace', fontSize: 12 }}>{v}</span> : '-'
                    },
                  ]}
                />
              </Card>
            )},

            { key: 'casefile', label: '病历', children: <CaseFileTab caseId={caseId} /> },
            ...(caseData.source === 'ai' || caseData.reviewStatus ? [
              { key: 'trace', label: '需求溯源', children: (
                <Card styles={{ body: { padding: 16 } }}>
                  {caseData.requirementPointIds?.length > 0 ? (
                    <div>
                      <h4 style={{ fontSize: 13, color: '#86909c', marginBottom: 8 }}>关联需求点</h4>
                      {caseData.requirementPointIds.map((rp, i) => (
                        <Tag key={i} color="blue" style={{ marginBottom: 4 }}>{rp}</Tag>
                      ))}
                    </div>
                  ) : (
                    <Empty description="无关联需求点" />
                  )}
                  {caseData.generationTaskId && (
                    <div style={{ marginTop: 12 }}>
                      <h4 style={{ fontSize: 13, color: '#86909c', marginBottom: 4 }}>生成任务</h4>
                      <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{caseData.generationTaskId}</span>
                    </div>
                  )}
                </Card>
              )},
              { key: 'archive', label: '生成档案', children: (
                <Card styles={{ body: { padding: 16 } }}>
                  <div style={{ textAlign: 'center', padding: 40, color: '#bfc4cd' }}>
                    <p>生成档案时间线 — 基于 case_gen_events 的事件序列</p>
                    <p style={{ fontSize: 12 }}>（generated/scored/reviewed/rejected/regenerated 事件将在此展示）</p>
                  </div>
                </Card>
              )},
            ] : []),
          ]} />
        </div>
      </div>

      <TemplateModal
        open={templateModalOpen}
        onClose={() => setTemplateModalOpen(false)}
        projectId={projectId}
        branchId={branchId}
        scenarioType={templateModalType}
        onSelect={(sc) => {
          if (templateModalType === 'api') {
            setApiScenario(sc)
            setApiScenarioStatus('draft')
          } else {
            setUiScenario(sc)
            setUiScenarioStatus('draft')
          }
          message.success('模板已导入，记得保存')
        }}
      />

      <Drawer
        title="场景变量"
        placement="right"
        width={720}
        open={svDrawerOpen}
        onClose={() => setSvDrawerOpen(false)}
        styles={{ body: { padding: 16 } }}
      >
        <ScenarioVariables projectId={projectId} branchId={branchId} caseId={caseId} />
      </Drawer>

      <Modal open={runModalOpen} onCancel={() => { setRunModalOpen(false); setRunResult(null); setRunStatus('idle') }} footer={null} title="执行用例" width={560}>
        <div style={{ padding: '12px 0' }}>
          <div style={{ padding: '12px 16px', background: 'rgba(0,0,0,0.02)', borderRadius: 12, marginBottom: 20 }}>
            <div style={{ fontWeight: 600, marginBottom: 4 }}>{title}</div>
            <div style={{ fontSize: 12, color: '#86909c', fontFamily: 'monospace' }}>{caseCode}</div>
          </div>
          <div style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 13, color: '#86909c', marginBottom: 8 }}>选择执行环境</div>
            <Select value={runEnv} onChange={setRunEnv} style={{ width: '100%' }} placeholder="请选择环境"
              options={environments.map(e => ({ value: e.id, label: e.name }))} />
          </div>
          {(scriptRefFile || hasActiveScript) ? (
            <div>
              {scriptRefFile && (
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 12, textAlign: 'center' }}>
                  脚本: <span style={{ fontFamily: 'monospace', color: '#4e5969' }}>{scriptRefFile}</span>
                </div>
              )}
              <div style={{ textAlign: 'center', marginBottom: runResult ? 16 : 0 }}>
                <Button type="primary" loading={runStatus === 'running'} disabled={!runEnv}
                  onClick={async () => {
                    if (!runEnv) { message.warning('请先选择执行环境'); return }
                    setRunStatus('running'); setRunResult(null)
                    try {
                      const res = await api.post(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/run?type=${type}`, { envId: runEnv })
                      setRunStatus('done')
                      setRunResult(res.data)
                    } catch (e) {
                      setRunStatus('error')
                      setRunResult({ status: 'error', errorSummary: e?.response?.data?.error?.message || e.message })
                    }
                  }}
                  icon={<PlayCircleOutlined />} style={{ minWidth: 160 }}>
                  快速执行
                </Button>
              </div>

              {runResult && (
                <div style={{ marginTop: 16 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 12 }}>
                    <Tag color={runResult.status === 'passed' ? undefined : runResult.status === 'failed' ? 'error' : 'warning'}
                      style={{ fontWeight: 700, fontSize: 13, padding: '2px 12px', ...(runResult.status === 'passed' ? { background: '#e0f7f6', color: '#0ea5a0', border: 'none' } : {}) }}>
                      {(runResult.status || 'UNKNOWN').toUpperCase()}
                    </Tag>
                    {runResult.durationMs != null && (
                      <span style={{ fontSize: 12, color: '#86909c' }}>耗时 {(runResult.durationMs / 1000).toFixed(1)}s</span>
                    )}
                  </div>

                  {runResult.errorSummary && (
                    <div style={{ padding: '10px 14px', background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: 12, marginBottom: 12 }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: '#e8453c', marginBottom: 4 }}>错误信息</div>
                      <pre style={{ margin: 0, fontSize: 12, color: '#434343', fontFamily: 'monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 150, overflow: 'auto' }}>{runResult.errorSummary}</pre>
                    </div>
                  )}

                  {runResult.stdout && (
                    <details>
                      <summary style={{ cursor: 'pointer', fontSize: 12, color: '#86909c', marginBottom: 6, userSelect: 'none' }}>执行日志</summary>
                      <pre style={{ margin: 0, padding: 12, background: '#1e1e1e', color: '#d4d4d4', borderRadius: 12, fontSize: 11, fontFamily: 'monospace', maxHeight: 250, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{runResult.stdout}</pre>
                    </details>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div style={{ textAlign: 'center', padding: '12px 0' }}>
              <div style={{ color: '#86909c', marginBottom: 12 }}>当前用例没有关联脚本</div>
              <div style={{ fontSize: 12, color: '#86909c' }}>请先在「接口测试」→「代码视图」中生成并保存脚本</div>
            </div>
          )}
        </div>
      </Modal>
    </div>
  )
}


function CaseFileTab({ caseId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!caseId) return
    setLoading(true)
    api.get(`/cases/${caseId}/file`).then(res => setData(res.data)).catch(() => {}).finally(() => setLoading(false))
  }, [caseId])

  if (loading) return <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
  if (!data) return <Empty description="无法加载病历" image={Empty.PRESENTED_IMAGE_SIMPLE} />

  const EVENT_LABELS = {
    ai_generated: { label: 'AI 生成', color: '#4e8af0', icon: '🔵' },
    ai_reviewed: { label: 'AI 评审', color: '#7c5cbf', icon: '🟡' },
    executed_pass: { label: '执行通过', color: '#0ea5a0', icon: '🟢' },
    executed_fail: { label: '执行失败', color: '#e8453c', icon: '🔴' },
    ai_diagnosed: { label: 'AI 诊断', color: '#fa8c16', icon: '🟠' },
    manual_edit: { label: '手动编辑', color: '#86909c', icon: '⚪' },
  }

  return (
    <Card styles={{ body: { padding: '16px 24px' } }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
        <Space>
          <span style={{ fontWeight: 600 }}>用例病历</span>
          {data.tags?.map(t => <Tag key={t} color={t === '#不稳定' ? 'error' : t === '#需要关注' ? 'warning' : 'default'}>{t}</Tag>)}
        </Space>
        {data.stats && (
          <Space size={16}>
            <span style={{ fontSize: 12, color: '#86909c' }}>执行 {data.stats.totalExecutions} 次</span>
            {data.stats.passRate !== null && (
              <span style={{ fontSize: 12, color: data.stats.passRate >= 80 ? '#0ea5a0' : '#e8453c' }}>
                通过率 {data.stats.passRate}%
              </span>
            )}
          </Space>
        )}
      </div>

      {data.events.length === 0 ? (
        <Empty description="暂无病历记录" image={Empty.PRESENTED_IMAGE_SIMPLE}>
          <span style={{ fontSize: 12, color: '#86909c' }}>用例被 AI 生成、评审、执行或诊断后会自动记录</span>
        </Empty>
      ) : (
        <div style={{ borderLeft: '2px solid rgba(0,0,0,0.04)', paddingLeft: 16, marginLeft: 8 }}>
          {data.events.map(e => {
            const cfg = EVENT_LABELS[e.eventType] || { label: e.eventType, color: '#86909c', icon: '⚪' }
            return (
              <div key={e.id} style={{ position: 'relative', paddingBottom: 16 }}>
                <div style={{ position: 'absolute', left: -24, top: 2, fontSize: 14 }}>{cfg.icon}</div>
                <div>
                  <Space size={8}>
                    <Tag color={cfg.color} style={{ fontSize: 11 }}>{cfg.label}</Tag>
                    <span style={{ fontSize: 12, color: '#86909c' }}>{e.createdAt?.slice(0, 16).replace('T', ' ')}</span>
                  </Space>
                  {e.summary && <div style={{ fontSize: 13, marginTop: 2 }}>{e.summary}</div>}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}
