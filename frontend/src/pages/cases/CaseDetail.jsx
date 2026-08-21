import { useState, useRef, useEffect, useCallback, useMemo } from 'react'
import { useParams, useNavigate, useLocation } from 'react-router-dom'
import { Card, Tag, Button, Input, Select, Space, Modal, Drawer, message, Tabs, Switch, Popover, Tooltip, Spin, Empty, Table, Alert } from 'antd'
import {
  ArrowLeftOutlined, PlayCircleOutlined, SaveOutlined,
  PlusOutlined, DeleteOutlined, HolderOutlined,
  ThunderboltOutlined, TagOutlined, AppstoreOutlined, ApiOutlined,
  FlagOutlined, WarningOutlined, CodeOutlined, CopyOutlined, FileTextOutlined,
  DesktopOutlined, CheckCircleOutlined, StarOutlined, StarFilled, ImportOutlined,
  DatabaseOutlined, CaretRightOutlined, BugOutlined, TagsOutlined, SearchOutlined,
} from '@ant-design/icons'
import { api, getValidToken } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'
import { useEnv, buildEnvOptions } from '../../utils/env'
import ScriptEditor from '../../components/ScriptEditor'
import ScenarioVariables from '../../components/ScenarioVariables'
import ApiStepList, { generateApiCodeFromSteps } from '../../components/ApiStepList'
import { scenarioToNodes, nodeToStepPatch } from './apiStepAdapter'
import RunResultPanel from '../../components/RunResultPanel'
import FailureTriagePanel from '../../components/FailureTriagePanel'
import { createSseParser } from '../../utils/sseParser'

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
// 状态体系 v2
const lifecycleMap = {
  draft: { label: '草稿', color: '#86909c', bg: 'rgba(0,0,0,0.03)' },
  done: { label: '完成', color: '#0ea5a0', bg: '#e0f7f6' },
  deprecated: { label: '废弃', color: '#e8453c', bg: '#fff2f0' },
}
// 三维统一状态（手动/UI/接口 共用）。
//
// **名字必须和徽标档位一字不差。** 徽标写「待发布」而下拉里同一个值叫「待审」——
// 人点开下拉看到高亮在「待审」，跟徽标对不上，只能理解成两个不同的东西。
// 实测被指出来过：`pending_review` 一处叫待发布、一处叫待审。
//
// **去掉了 needs_fix（原「待修改」）。** 它和「调试中」表达的是同一件事
// （这一维现在有问题、不能进回归），多一个态只是让人纠结该选哪个；
// 有问题直接改「调试中」或「草稿」。库里一条都没有（全库 0 条），删掉零成本。
const dimStatusMap = {
  draft: { label: '草稿', color: '#86909c', bg: 'rgba(0,0,0,0.03)' },
  debugging: { label: '调试中', color: '#faad14', bg: '#fffbe6' },
  completed: { label: '完成', color: '#0ea5a0', bg: '#e0f7f6' },
}
// 和列表页同一套口径（CaseManagement 的 tierOf，两处必须一致）。
//
// **原来是三档，把 pending_review 也归进「调试中」—— 那是错的。**
// pending_review 的含义是"平台跑绿了，轮到人发布"，跟"还在调/挂着"是相反的状态。
// 实测后果：AT-0011 19/19 全绿、api_status 已是 pending_review，徽标却写「接口·调试中」，
// 人看到的结论跟事实反过来，只能理解成"CC 还没做完"。
// 更别扭的是文字取自这里（三档）、颜色取自 dimStatusMap（六态），于是同样写
// 「调试中」颜色却不同 —— 一个蓝一个黄，同一屏两个词自相矛盾。
//
// 现在四档，且**颜色跟着档位走**，不再跟 dimStatusMap 混用。
// **档位只看存储的状态，不再拿 hasContent 派生。**
//
// 派生是三次「徽标和下拉对不上」的根源：徽标显示派生值、下拉显示存储值，
// 只要有派生，两者就永远可能不一致（实测手动维度徽标「已写」、下拉「未开始」）。
// 现在写步骤/跑脚本的时候就把状态落对（见后端 sync_manual_status / apply_case_status），
// 显示层只负责翻译，不负责猜。
//
// 唯一保留的派生：状态是 not_started 但确实有内容 —— 那是**旧数据**
// （2026-08 之前写的步骤没人推进过状态），标成「已写」比标「未开始」诚实。
// **档位表没有了 —— 三维只有 3 态，直接显示存储值。**
// 原来 5 态压成档位再显示，是三次「徽标和下拉对不上」的根源。
// 现在 dimStatusMap 既是下拉的选项、也是徽标的文字和颜色，只有一份。
const dimLabel = (status) => dimStatusMap[status] || dimStatusMap.draft

// 覆盖层级：这条用例打算做到哪一步。
const TARGET_LEVEL = {
  spec:     { label: '只做步骤',   hint: '只要手工步骤' },
  spec_api: { label: '步骤+接口', hint: '手工步骤 + 接口场景，不做 UI' },
  full:     { label: '三件套',     hint: '手工步骤 + 接口场景 + UI 脚本' },
}
const dimPlanned = (targetLevel, dim) => {
  const t = targetLevel || 'spec'
  if (dim === 'manual') return true
  if (dim === 'api') return t === 'spec_api' || t === 'full'
  return t === 'full'
}
// **「本来就不做」和「还没做」不能长得一样。** 原来 spec_api 的用例显示
// 「UI·草稿」，看着就是没做完 —— 而那一维不在计划里，永远不会变成「完成」，
// 人却会一直等它变。不新增状态值（库里仍是 draft）：「做不做」是规划意图，
// target_level 已经表达了，显示层读它翻译即可。
const NOT_PLANNED = { label: '无', color: '#c9cdd4', bg: 'rgba(0,0,0,0.03)' }
const dimBadge = (targetLevel, dim, status) =>
  dimPlanned(targetLevel, dim) ? dimLabel(status) : NOT_PLANNED

// 审核标签（用例级，一个）。**NULL 就是「待提审」** —— 不存值，因为绝大多数用例
// 都在这个态，存了等于给每条都挂个灰标签，列表上一片噪音。
// 「待审」是三维全完成后**自动进**的，没有「提交审核」那一下。
// 审没审**不挡回归**：建计划直接能跑。
const REVIEW = {
  pending:  { label: '待审',   color: '#4e8af0', bg: '#eef4ff' },
  approved: { label: '已审',   color: '#0ea5a0', bg: '#e0f7f6' },
  rejected: { label: '不通过', color: '#e8453c', bg: '#fff2f0' },
}
const REVIEW_KEYS = ['pending', 'approved', 'rejected']
// 维度名两种拼法都要认：库里存的是 snake_case，而响应经过全局 camelize
// 变成了 scenarioSanity 这种。只写一种的话页面上会出现「纪律 90 / apiNecessity 90」
// 中英混排（实测就是这样）。
const DIM_LABEL = {
  scenario_sanity: '场景合理性', scenarioSanity: '场景合理性',
  verification_depth: '验证点到位', verificationDepth: '验证点到位',
  api_necessity: '接口必要性', apiNecessity: '接口必要性',
  ui_correctness: 'UI 脚本', uiCorrectness: 'UI 脚本',
  self_coverage: '本条覆盖', selfCoverage: '本条覆盖',
  discipline: '纪律',
}

const DIM_STATUS_KEYS = ['draft', 'debugging', 'completed']

// 步骤字段的**唯一归一化入口**。
//
// 同一个字段有两套名字，取决于数据从哪来：
//   · 运行时（SSE step_start 事件）→ `action` / `phase` / `duration_ms`
//   · 页面加载 / 执行历史（走 HTTP，被驼峰中间件改过）→ `stepName` / `stepPhase` / `durationMs`
// 三个读取点原来各写一遍 `s.step_name || s.action || ...`，都漏了驼峰那一套，
// 于是**刷新页面之后每一步都显示成「步骤 1 通过」** —— 名字、耗时、错误全丢，
// 比不显示步骤更糟（看着像有信息，其实什么都没有）。实测被指出来。
//
// 所以只留这一个函数，谁要读步骤都从它拿。
const stepInfo = (s, i) => ({
  name: s.stepName || s.step_name || s.action || s.step || `步骤 ${i + 1}`,
  phase: s.stepPhase || s.step_phase || s.phase,
  ms: s.durationMs ?? s.duration_ms,
  error: s.errorSummary || s.error_summary || s.error,
  status: s.status,
})

// 失败现象码 → 人话。
// ⚠ 这里的键是**数据**不是字段名，但响应层会把 JSON 里的字典键一并驼峰化
// （库里存的是 assertion_mismatch，到前端变成 assertionMismatch），所以两种写法都认；
// 认不出来的原样显示，别吞掉 —— 出现新现象码时要看得见，而不是显示成空白。
const PHENOMENON_LABELS = {
  timeout: '超时',
  element_not_found: '元素找不到',
  assertion_mismatch: '断言不符',
  http_5xx: '接口 5xx',
  script_error: '脚本自身报错',
  dependency_unresolved: '依赖没解析出来',
  unknown: '看不出来',
}

/** 执行历史里那一次的步骤清单。 */
function RunSteps({ steps }) {
  if (!steps?.length) return <div style={{ fontSize: 12, color: '#86909c' }}>本次没有步骤记录</div>
  return (
    <div style={{ maxHeight: 340, overflow: 'auto', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 10 }}>
      {steps.map((s, i) => {
        const bad = s.status && s.status !== 'passed'
        const { name: sName, phase: ph, ms: sMs, error: sErr } = stepInfo(s, i)
        return (
          <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'baseline', padding: '4px 10px', fontSize: 12,
            borderTop: i ? '1px solid rgba(0,0,0,0.03)' : 'none', background: bad ? '#fff5f5' : 'transparent' }}>
            <span style={{ color: '#c9cdd4', minWidth: 20, textAlign: 'right', fontFamily: 'var(--font-mono)' }}>{i + 1}</span>
            <span style={{ fontSize: 10, padding: '0 5px', borderRadius: 6, lineHeight: '17px', flexShrink: 0,
              background: ph === 'verify' ? 'rgba(78,138,240,0.1)' : 'rgba(0,0,0,0.04)',
              color: ph === 'verify' ? '#4e8af0' : '#86909c' }}>
              {ph === 'verify' ? '验证' : ph === 'setup' ? '前置' : '操作'}
            </span>
            <span style={{ flex: 1, color: bad ? '#e8453c' : '#1d2129', wordBreak: 'break-all' }}>
              {sName}
              {sErr && <div style={{ color: '#e8453c', fontFamily: 'var(--font-mono)', fontSize: 11, marginTop: 2 }}>{sErr}</div>}
            </span>
            <span style={{ color: '#c9cdd4', flexShrink: 0, fontFamily: 'var(--font-mono)' }}>{sMs != null ? `${sMs}ms` : ''}</span>
          </div>
        )
      })}
    </div>
  )
}

/** 执行历史展开后的内容 —— 页签，不是一路摊开。
 *
 * 原来步骤清单和流量清单是**上下摞着同时展开**的：一条记录 37 步 + 98 条流量，
 * 展开一行就占掉三四屏，往下翻第二条记录得滚很久。而且每条历史都长一样，
 * 摊开并不会让人「一眼看全」，只会让人「怎么翻都翻不完」。
 * 跟「UI 测试」页签里的 脚本 / 执行轨迹 / 本次流量 保持同一种形态。
 *
 * 失败归因**不进页签**，留在最上面：那是失败时唯一要人动手的东西，
 * 藏进页签就等于藏起来了。
 */
function RunDetail({ run, projectId, branchId, caseId, onConfirmed }) {
  const r = run
  const traffic = r.capturedRequests || r.captured_requests
  const pruned = r.capturedPrunedCount ?? r.captured_pruned_count
  const passedSteps = (r.steps || []).filter(s => s.status === 'passed').length
  const label = (t, n) => <span style={{ fontSize: 12.5 }}>{t}{n != null && <span style={{ color: '#86909c' }}>（{n}）</span>}</span>

  const items = []
  if (r.steps?.length) {
    items.push({ key: 'steps', label: label('执行步骤', `${passedSteps}/${r.steps.length} 通过`),
      children: <RunSteps steps={r.steps} /> })
  }
  items.push({
    key: 'traffic',
    label: label('本次流量', traffic?.length ?? (pruned != null ? '已回收' : '无')),
    children: <RunTraffic run={r} />,
  })
  if (r.screenshots?.length) {
    items.push({ key: 'shots', label: label('截图', r.screenshots.length), children: (
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        {r.screenshots.map((s, i) => (
          <div key={i} style={{ borderRadius: 8, overflow: 'hidden', border: '1px solid rgba(0,0,0,0.06)', cursor: 'pointer' }}
            onClick={() => window.open(`data:image/png;base64,${s.base64}`, '_blank')}>
            <img src={`data:image/png;base64,${s.base64}`} alt={s.name}
              style={{ width: 160, height: 100, objectFit: 'cover', display: 'block' }} />
            <div style={{ fontSize: 10, color: '#86909c', padding: '1px 4px', background: 'rgba(0,0,0,0.02)' }}>{s.name}</div>
          </div>
        ))}
      </div>
    ) })
  }
  if (r.stdout) {
    // 接口执行落的是给人看的中文轨迹，UI 执行落的是 pytest 原文 —— 标签得说清是哪个，
    // 不然人点进去看到一屏 pytest 横幅，不知道自己点错了还是本来就这样。
    const isApi = r.scriptType === 'api'
    items.push({ key: 'raw', label: label(isApi ? '执行轨迹' : '原始日志'), children: (
      <pre style={{ margin: 0, padding: 12, borderRadius: 12, fontSize: 12, fontFamily: 'var(--font-mono)',
        maxHeight: 340, overflow: 'auto', whiteSpace: 'pre-wrap', lineHeight: 1.7,
        background: isApi ? 'rgba(0,0,0,0.02)' : '#1e1e1e', color: isApi ? '#1d2129' : '#d4d4d4' }}>{r.stdout}</pre>
    ) })
  }

  return (
    <div>
      {r.status !== 'passed' && (
        <FailureTriagePanel projectId={projectId} branchId={branchId} caseId={caseId} run={r} onConfirmed={onConfirmed} />
      )}
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
        {r.failurePhenomenon && (
          <Tag color={r.status === 'passed' ? undefined : 'error'} style={{ margin: 0 }}>
            {phenomenonLabel(r.failurePhenomenon)}
          </Tag>
        )}
        {r.errorSummary
          ? <span style={{ fontSize: 12.5, color: '#e8453c', fontFamily: 'var(--font-mono)' }}>{r.errorSummary}</span>
          : <span style={{ fontSize: 12.5, color: '#86909c' }}>脚本跑完没有报错</span>}
      </div>
      {items.length
        ? <Tabs size="small" items={items} destroyOnHidden />
        : <span style={{ color: '#c9cdd4' }}>本次没有留下任何记录</span>}
    </div>
  )
}

/** 执行历史里那一次的网络流量。
 *
 * 此前这里什么都不渲染 —— 库里每次执行都单独存了一份 96~98 条的流量、接口也一直
 * 在返回，界面上却只有「UI 测试」页签里最新那一次看得到，跑下一次就被顶掉。
 * 存了没人能读，等于白存。
 *
 * 三种情况要分得开，**尤其是后两种不能都渲染成空白**：
 *   有流量   → 列出来
 *   已回收   → 说清楚是回收了、原来有多少条、为什么
 *   没抓到   → 说没抓到
 * 「已回收」显示成空白的话，人会当成 bug 报 —— 这个项目已经栽在「静默」上三次。
 */
function RunTraffic({ run }) {
  const list = run.capturedRequests || run.captured_requests
  const pruned = run.capturedPrunedCount ?? run.captured_pruned_count
  const box = { marginTop: 10, fontSize: 12, color: '#86909c' }
  if (!list?.length) {
    // 条数可能是 null（老数据修不回来了），那就只说回收了、不报数 ——
    // 「0 条流量已回收」比不报数更让人摸不着头脑。
    if (pruned != null) {
      return (
        <div style={box}>
          {pruned > 0 ? `本次 ${pruned} 条流量已回收` : '本次流量已回收'}
          （通过的只留最新一次，失败的留最近 5 次）—— 步骤、错误和截图都还在。
        </div>
      )
    }
    return <div style={box}>本次没有抓到流量</div>
  }
  return (
    <div style={{ marginTop: 10, border: '1px solid rgba(0,0,0,0.06)', borderRadius: 10, overflow: 'hidden' }}>
      <div style={{ padding: '6px 10px', background: '#fafbfc', fontSize: 12, color: '#4e5969', fontWeight: 600 }}>
        本次流量（{list.length} 条）
      </div>
      <div style={{ maxHeight: 260, overflow: 'auto' }}>
        {list.map((q, i) => {
          const code = q.status ?? q.statusCode
          const bad = code && code >= 400
          return (
            <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'baseline', padding: '3px 10px',
              fontSize: 11.5, fontFamily: 'var(--font-mono)',
              borderTop: i ? '1px solid rgba(0,0,0,0.03)' : 'none',
              background: bad ? '#fff5f5' : 'transparent' }}>
              <span style={{ minWidth: 46, color: '#4e5969' }}>{q.method}</span>
              <span style={{ flex: 1, wordBreak: 'break-all', color: '#1d2129' }}>{q.url}</span>
              <span style={{ color: bad ? '#e8453c' : '#86909c' }}>{code ?? '-'}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function phenomenonLabel(k) {
  // 驼峰化把 http_5xx 变成 http5xx（下划线后是数字，没有大写可转），
  // 所以字母边界和数字边界都要还原，否则这一条永远落回英文。
  const snake = String(k || '')
    .replace(/([a-z])([A-Z])/g, '$1_$2')
    .replace(/([a-zA-Z])(\d)/g, '$1_$2')
    .toLowerCase()
  return PHENOMENON_LABELS[snake] || PHENOMENON_LABELS[k] || k
}

function InlineProp({ icon, value, color, bg, children }) {
  const [open, setOpen] = useState(false)
  return (
    <Popover open={open} onOpenChange={setOpen} trigger="click" placement="bottomLeft"
      // **必须有 maxWidth。** 只给 minWidth 的话，内容里一段长文本（「预期已确认」
      // 那条备注就是一整段话）会把盒子撑到上千像素宽，而这些标签挨着头部右边，
      // antd 放不下就往左挤 —— 实测整个气泡盖到左侧导航上，文字还溢出屏幕外。
      // 限宽 + 允许折行，长文本自己换行，气泡就不会越界。
      content={<div style={{ minWidth: 150, maxWidth: 460, whiteSpace: 'normal',
        wordBreak: 'break-word', overflowWrap: 'anywhere' }}
        onClick={e => e.stopPropagation()}>{children}</div>}
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
            <span style={{ flex: 1, fontSize: 12, fontFamily: 'var(--font-mono)', color: extraColor || '#0ea5a0' }}>
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
          <span style={{ fontFamily: 'var(--font-mono)', color: '#4e5969' }}>{scriptData.filePath}</span>
          {scriptData.funcName && <Tag color="blue" style={{ fontSize: 11, margin: 0 }}>{scriptData.funcName}</Tag>}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Tag style={{ fontSize: 11, margin: 0, fontFamily: 'var(--font-mono)' }}>{scriptData.commitSha?.substring(0, 8)}</Tag>
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
          fontFamily: 'var(--font-mono)', color: '#d4d4d4',
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
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: '#4e5969' }}>{scenario.scriptRefFile}</span>
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
              <Tag key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: 12, background: '#edf3ff', border: '1px solid rgba(78,138,240,0.3)', color: '#4e8af0', borderRadius: 12, padding: '2px 8px' }}>{v}</Tag>
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
  // 「运行全部」跑哪几步。null = 全选（默认）；Set = 只跑这些 id。
  // 只活在这次会话里，不落库 —— 步骤自己的 enabled 才是持久禁用。
  const [runSelection, setRunSelection] = useState(null)


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
  //
  // **顺序要单独发一次**。逐个 PUT 只带内容，`sort_order` 不在 patch 里
  // （后端 UpdateStepRequest 也不收），而新建步骤一律 max+1 落到末尾 ——
  // 所以拖完保存、刷新回来顺序纹丝不动。而顺序不是显示问题：执行器按
  // sort_order 跑，第 1 步登录取的 token 后面才有得用、清理步骤得在最后。
  // 顺序错了的表现是后面一片「变量未解析」，人还会以为是变量没定义。
  const saveNodes = async (nodes) => {
    if (!scenario) return
    const sid = scenario.id
    const prev = scenario.steps || []
    try {
      // 边写边记最终顺序。新建的 id 要从 POST 响应里接住，否则它不在排序名单里，
      // 只能留在末尾 —— 那正是「复制出来的副本跑到最后」的老毛病。
      const orderedIds = []
      for (const n of nodes) {
        const patch = nodeToStepPatch(n)
        if (n.id) {
          await api.put(`${base}/${sid}/steps/${n.id}`, patch)
          orderedIds.push(n.id)
        } else {
          const res = await api.post(`${base}/${sid}/steps`, patch)
          const newId = res?.data?.id
          if (newId) orderedIds.push(newId)
        }
      }
      const keep = new Set(nodes.filter(n => n.id).map(n => n.id))
      for (const st of prev) {
        if (!keep.has(st.id)) await api.delete(`${base}/${sid}/steps/${st.id}`)
      }
      if (orderedIds.length) {
        await api.put(`${base}/${sid}/steps/reorder`, { stepIds: orderedIds })
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
    // runSelection == null 就是全选，不传 stepIds（跟"每一步都勾着"等价，
    // 但少一次把新增步骤漏掉的机会）。
    if (runSelection != null && runSelection.size === 0) {
      message.warning('一个步骤都没勾选，没有可执行的内容'); return
    }
    setRunning(true); setResult(null); setStepResults([]); setReportId(null); setPrecheck(null); setShowPanel(true)
    api.stream(`${base}/run`, {
      scenarioIds: [scenario.id], envId: runEnv,
      ...(runSelection != null ? { stepIds: [...runSelection] } : {}),
    }, {
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
          {/* 编排场景**没有独立编号**，它的 code 就是用例编号 —— 一个用例只有一条
              编排场景，再发一个 AT-0011 只是给同一件东西起第二个名字，而那个号还是
              从「接口测试」模块的序列里领的（人去那个页面搜还搜不到）。
              所以这里不再印编号：标题栏上方的面包屑已经写着用例编号了，重复一遍是噪音。
              AT-#### 从此只属于单接口场景。 */}
          {scenario && scenario.code && scenario.code.startsWith('AT-') && (
            // 没绑用例的孤儿场景才会是 AT-####，这种要标出来 —— 它不属于任何用例。
            <Tooltip title="这条场景的编号还是 AT-####，说明它没有绑定用例（孤儿场景）。正常回推的编排场景编号就是用例编号。">
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: '#fa8c16',
                borderBottom: '1px dotted #ffd591', cursor: 'help' }}>
                未绑定用例 · {scenario.code}
              </span>
            </Tooltip>
          )}
          {scenario && <span style={{ fontSize: 12, color: '#86909c' }}>{(scenario.steps || []).length} 个请求</span>}
          {result && (
            <Tag color={result.passed ? 'success' : 'error'} style={{ margin: 0, cursor: 'pointer' }}
              onClick={() => setShowPanel(true)}>
              {/* 原来写「失败 12/13」——「失败」后面跟一个"通过数/总数"，
                  读起来像"失败了 12 条"，而实际是"12 步通过、1 步失败"。
                  挂了的时候人最想知道的是**挂了几步**，直接说那个数。 */}
              {result.passed
                ? `全通过 ${result.passCount ?? 0}/${(result.passCount ?? 0) + (result.failCount ?? 0)} 步`
                : `${result.failCount ?? 0} 步失败（共 ${(result.passCount ?? 0) + (result.failCount ?? 0)} 步）`} · 看详情
            </Tag>
          )}
        </div>
        <Space size={6}>
          <Select size="small" value={runEnv} onChange={onEnvChange} style={{ width: 170 }}
            popupMatchSelectWidth={false} placeholder="执行环境" options={buildEnvOptions(environments)} />
          <Button size="small" onClick={load} loading={loading}>刷新</Button>
          {scenario && (
            <Button size="small" type="primary" ghost icon={<PlayCircleOutlined />} loading={running}
              // 一个都没勾就直接禁用。留着能点、点了弹个告警，等于让人先做一次
              // 无效操作再被告知 —— 按钮本身就该说明这时候没东西可跑。
              disabled={runSelection != null && runSelection.size === 0}
              style={runSelection != null && runSelection.size === 0
                ? undefined : { color: '#0ea5a0', borderColor: '#0ea5a0' }}
              onClick={run}>
              {/* 勾掉过步骤就把真实条数写在按钮上 ——「运行全部」这四个字
                  在只跑一部分的时候是句假话，而这正是最容易误读结果的时刻。 */}
              {runSelection != null ? `运行选中 (${runSelection.size})` : '运行全部'}
            </Button>
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
          runSelection={runSelection}
          onRunSelectionChange={setRunSelection}
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

      {/* 右侧抽屉。曾经试过排在文档流里（步骤编辑区很高，结果落到屏幕外，像没反应）
          和吸底（能看见但形态怪），都不对，回到 Drawer。
          （原注释说"跟「接口测试」模块页保持一致"—— 那个模块 2026-08-15 下线了，
            这里现在是接口场景运行结果**唯一**的呈现方式。） */}
      <Drawer
        open={showPanel && (stepResults.length > 0 || running)}
        onClose={() => setShowPanel(false)}
        placement="right"
        width={560}
        mask={false}
        closable={false}
        styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column', height: '100%' } }}
      >
        <RunResultPanel
          results={stepResults}
          scenario={scenario}
          running={running}
          reportId={reportId}
          projectId={projectId}
          envName={(environments.find(e => e.id === runEnv) || {}).name}
          onClose={() => setShowPanel(false)}
        />
      </Drawer>
    </Card>
  )
}

function ScenarioEditor({
  scenario, setScenario, dimStatus, setDimStatus,
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
  const [previewScreenshot, setPreviewScreenshot] = useState(null)
  const [showNoiseSteps, setShowNoiseSteps] = useState(false)
  // 「脚本」是唯一永远有内容的视图，所以它是默认。另两个视图有数据才出现页签，
  // 停在某个视图时它的数据没了（重新运行清空），activeKey 会指向不存在的 key，
  // 页面就空了 —— 所以下面渲染时做一次兜底。
  const [uiView, setUiView] = useState('script')
  const [selectedApis, setSelectedApis] = useState([])
  const [apiArranging, setApiArranging] = useState(false)
  const [expandedApi, setExpandedApi] = useState(null)  // 接口视图展开查看请求/响应详情的行索引
  const [debugHistory, setDebugHistory] = useState([])
  // 智能识别：从抓包里算出「编排一个场景」推荐勾选哪些接口 + 每条理由（写操作/依赖/噪音）
  const apiAnalysis = useMemo(() => analyzeApiRequests(debugResult?.captured_requests || []), [debugResult?.captured_requests])
  // 流量被截断时后端会在末尾留一条标记（见 engine/har.py）。不显示的话
  // 面板上的条数就是上限本身，人会当成真实条数。
  const trunc = useMemo(
    () => (debugResult?.captured_requests || []).find(r => r?.truncated) || null,
    [debugResult?.captured_requests])
  const scriptEditorRef = useRef(null)

  const loadDebugHistory = async () => {
    try {
      const res = await api.get(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/runs?type=ui&limit=10`)
      setDebugHistory((res.data || []).filter(r => r.status !== 'passed'))
    } catch { /* ignore */ }
  }

  // 流式运行脚本 — 实时推送步骤进度
  const [liveSteps, setLiveSteps] = useState([])
  // 收尾阶段的提示。最后一步跑完之后还有 2 秒左右在关浏览器、把 HAR 落盘、解析 junit，
  // 这期间一个事件都没有 —— 面板停在「37 步完成，等待中...」，**看着就是卡死了**，
  // 实测被当成 bug 报了两次。后端发了 finishing 事件，前端得认它。
  const [finishingMsg, setFinishingMsg] = useState(null)
  const liveStepsRef = useRef([])
  const abortRef = useRef(null)

  const stopExecution = () => {
    if (abortRef.current) {
      abortRef.current.abort()
      abortRef.current = null
    }
    setDebugRunning(false)
    setFinishingMsg(null)
    setLiveSteps(prev => prev.map(s => s.status === 'running' ? { ...s, status: 'cancelled', error: '用户取消' } : s))
    setDebugResult(prev => prev ? { ...prev, status: 'cancelled' } : prev)
    message.info('已停止执行')
  }

  const runScriptWithStream = (onDone) => {
    const url = `/api/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/run-stream?type=ui`
    const controller = new AbortController()
    abortRef.current = controller
    setLiveSteps([])
    setFinishingMsg(null)
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
      let sawDone = false

      // 解析器建在循环外 —— 事件名要活过网络分片。done 那一帧 47KB，装不进一个
      // 分片，事件名放在读循环里会被清掉，整帧静默丢弃。见 sseParser.js。
      const parser = createSseParser((ev, data) => {
        if (ev === 'step_start') {
          // 又有步骤了 → 刚才那次沉默不是收尾，把提示撤掉。
          setFinishingMsg(null)
          setLiveSteps(prev => { const n = [...prev, { ...data, status: 'running' }]; liveStepsRef.current = n; return n })
        } else if (ev === 'step_end') {
          setLiveSteps(prev => { const n = prev.map(s => s.seq === data.seq ? { ...s, ...data } : s); liveStepsRef.current = n; return n })
        } else if (ev === 'finishing') {
          setFinishingMsg(data.message || '正在收尾…')
        } else if (ev === 'done') {
          sawDone = true
          setFinishingMsg(null)
          // 优先用 liveSteps（有完整步骤名），fallback 到 data.steps
          const live = liveStepsRef.current
          const steps = live.length > 0 ? live : (data.steps || [])
          // 运行结果不含接口流量 → 保留生成时抓到的接口，别把「接口视图」清空
          // 后端现在统一驼峰（SSE 也过 to_camel_case 了），两种都认 ——
          // 内部沿用 snake 那套键，跟 918 行的归一化保持一致。
          const cap = data.capturedRequests || data.captured_requests
          setDebugResult(prev => ({ ...data, steps, captured_requests: (cap?.length ? cap : prev?.captured_requests) || [], _drawerOpen: true }))
          setDebugRunning(false)
          setLiveSteps([]); liveStepsRef.current = []
          scriptEditorRef.current?.refresh()
          onDone?.(data)
        }
      })

      function processChunk() {
        reader.read().then(({ done, value }) => {
          if (done) {
            // 流断了却没收到 done —— 转圈状态必须自己收掉，否则又是「一直这样」。
            // 宁可显示一句「连接中断」让人重跑，也不能永远转圈假装还在跑。
            if (!sawDone) {
              setFinishingMsg(null)
              setDebugRunning(false)
              setDebugResult(prev => ({
                ...(prev || {}), status: 'error',
                errorSummary: '连接在拿到结果前断开了；执行本身可能已完成，去「执行历史」看这一次的记录。',
                steps: liveStepsRef.current.length ? liveStepsRef.current : (prev?.steps || []),
                _drawerOpen: true,
              }))
            }
            return
          }
          parser.push(decoder.decode(value, { stream: true }))
          processChunk()
        }).catch(() => { setFinishingMsg(null); setDebugRunning(false) })
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
        /* 平台侧「AI 生成脚本」入口已下线：实测几十次没跑通过，弱模型 + 管道崩 + 执行器精分。
           脚本改由外部 Claude Code 在本地写好、跑通，再经 tb_sync_ui_script 回推进来。
           平台只负责存、跑、留痕——它擅长的部分。 */
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 14, padding: '32px 0' }}>
          <DesktopOutlined style={{ fontSize: 40, color: 'rgba(124,92,191,0.25)' }} />
          <div style={{ fontSize: 14, color: '#4e5969', fontWeight: 500 }}>
            该用例还没有 UI 脚本（手动测试步骤 {manualSteps.length} 步）
          </div>
          <div style={{ fontSize: 12, color: '#86909c', maxWidth: 460, textAlign: 'center', lineHeight: 1.7 }}>
            UI 脚本由 Claude Code 在本地写好并跑通后回推进来，平台负责存、跑、留痕。<br />
            在连了本平台 MCP 的 Claude Code 里说：<br />
            <span style={{
              display: 'inline-block', marginTop: 6, padding: '4px 10px', borderRadius: 6,
              background: 'rgba(0,0,0,0.04)', fontFamily: 'var(--font-mono)', fontSize: 11, color: '#4e5969',
            }}>
              把用例「{caseTitle}」的 UI 脚本写出来并回推（case_id={caseId}）
            </span>
          </div>
          <div style={{ fontSize: 12, color: '#c9cdd4' }}>
            它会先调 tb_get_sync_spec(kind='ui_script') 对齐写法，再用 tb_sync_ui_script 入库
          </div>
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
              // 接口返回的是 capturedRequests（驼峰），这里统一成运行时那套 snake key，
              // 否则「接口视图」只有当场点过运行验证才有内容 —— 库里明明存着流量。
              setDebugResult({
                ...last,
                durationMs: last.durationMs ?? last.duration_ms,
                errorSummary: last.errorSummary ?? last.error_summary,
                captured_requests: last.capturedRequests || last.captured_requests || [],
                failurePhenomenon: last.failurePhenomenon,
              })
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
            {/* 原来用 `durationMs &&` 判：0 是 falsy，于是"跑得飞快"和"没记耗时"
                都变成整块消失，抽屉里那一栏空着 —— 用户以为是坏了。
                有值就显示，没有就明说"未记录"。 */}
            <span style={{ fontSize: 12, color: '#86909c' }}>
              {debugResult.durationMs != null
                ? `${(debugResult.durationMs / 1000).toFixed(1)}s`
                : '耗时未记录'}
            </span>
            {debugResult.steps?.length > 0 && (
              <span style={{ fontSize: 12, color: '#86909c' }}>
                {debugResult.steps.filter(s => s.status === 'passed').length}/{debugResult.steps.length} 步通过
              </span>
            )}
            {debugResult.captured_requests?.length > 0 && (
              // 这是本次**抓到的请求条数**，不是"覆盖了多少个接口"——
              // 同一个接口被调 10 次也算 10 条。写成"个接口"会让人以为这脚本
              // 覆盖了 93 个接口，实际可能就三五个。
              //
              // 截断了就必须说出来：此前静默截断，面板写「抓到 150 条」而 150 恰好
              // 是上限，人读成"这次发了 150 条"。被丢掉的还是时间上更靠后、业务上
              // 更关键的那些（实测丢了 publish）。
              <Tooltip title={trunc
                ? `这次实际发出 ${trunc.totalSeen} 条，只留存了前 ${trunc.kept} 条，后面的请求没有记录 —— 靠后的写操作可能不在里面`
                : "本次执行期间抓到的 HTTP 请求条数（同一接口被调多次算多条），不是覆盖的接口数量"}>
                <span style={{ fontSize: 12, color: trunc ? '#fa8c16' : '#86909c', borderBottom: '1px dotted #d9d9d9' }}>
                  抓到 {debugResult.captured_requests.filter(r => !r.truncated).length} 条请求
                  {trunc ? ` · 已截断（共 ${trunc.totalSeen} 条）` : ''}
                </span>
              </Tooltip>
            )}
            {/* 失败现象是平台按确定性规则判好的「是什么」，一直存在库里没送到页面。
                人扫一眼就知道往哪看，不用去读一坨 pytest stdout。 */}
            {!passed && debugResult.failurePhenomenon && (
              <Tag color="error" style={{ margin: 0, fontSize: 11 }}>
                {phenomenonLabel(debugResult.failurePhenomenon)}
              </Tag>
            )}
            <span style={{ fontSize: 11, color: '#c9cdd4', marginLeft: 'auto' }}>点击查看详情</span>
          </div>
        )}

        {/* 脚本永远在，另两个视图只有真有数据才出现。
            此前三个页签常驻，而「执行轨迹」全平台 0/204 条用例有数据（它的生产者是
            已封存的平台侧 UI 生成），「接口视图」也要当场跑过才有 —— 打开就是两个空页签，
            人得挨个点一遍才知道哪个是活的。有内容才给页签，角标直接写清有多少。 */}
        <Tabs size="small" style={{ marginBottom: 0 }}
          activeKey={
            (uiView === 'steps' && !(liveSteps.length || debugResult?.steps?.length)) ||
            (uiView === 'api' && !debugResult?.captured_requests?.length)
              ? 'script' : uiView
          }
          onChange={setUiView}
          items={[
            { key: 'script', label: '脚本', children: (
              <div style={{ position: 'relative' }}>
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
            ...(!(liveSteps.length || debugResult?.steps?.length) ? [] : [
            { key: 'steps', label: `执行轨迹 (${liveSteps.length > 0 ? liveSteps.length : debugResult.steps.length})`, children: (
              <div style={{ padding: '12px 0' }}>
                {(debugResult?.steps || liveSteps || []).length > 0 ? (() => {
                  const allSteps = liveSteps.length > 0 ? liveSteps : (debugResult?.steps || [])
                  // 探索类噪音（快照/JS求值/查找/网络/关闭浏览器/Glob）默认折叠，突出真实动作；失败步骤始终显示
                  const NOISE = /^(获取页面快照|执行 JS|关闭浏览器|Glob|browser_find|browser_network|browser_snapshot|browser_evaluate|Read|Bash)/
                  const isNoise = (s, i) => NOISE.test(stepInfo(s, i).name) && s.status !== 'failed'
                  const hiddenCount = allSteps.filter((s, i) => isNoise(s, i)).length
                  const shownSteps = showNoiseSteps ? allSteps : allSteps.filter((s, i) => !isNoise(s, i))
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
                      const name = stepInfo(s, i).name
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
                          </div>
                          {s.duration_ms != null && <span style={{ fontSize: 11, color: '#c9cdd4', flexShrink: 0 }}>{s.duration_ms >= 1000 ? `${(s.duration_ms / 1000).toFixed(1)}s` : `${s.duration_ms}ms`}</span>}
                        </div>
                      )
                    })}
                  </div>
                  )})() : (
                  <div style={{ padding: 32, textAlign: 'center', color: '#c9cdd4' }}>点击「运行验证」后查看执行步骤</div>
                )}
              </div>
            )}]),
            ...(!(debugResult?.captured_requests?.length) ? [] : [
            { key: 'api', label: `本次流量 (${debugResult.captured_requests.filter(r => !r.truncated).length})`, children: (
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
                          // 先看这条用例是不是已经有场景了。
                          // 「一个用例 = 一个接口场景」是这个页面的前提（下面只显示步骤最多的
                          // 那一条），所以已有的时候必须问清楚是接上去还是换掉 ——
                          // 此前是默默再建一条：新的步骤多就把原来跑通的那条顶掉，
                          // 步骤少就自己隐身，两种都不出声。
                          let mode = null
                          try {
                            const ex = await api.get(`/projects/${projectId}/branches/${branchId}/api-tests?source_case_id=${caseId}`)
                            const list = ex.data || []
                            if (list.length) {
                              const head = [...list].sort((a, b) => (b.stepCount || 0) - (a.stepCount || 0))[0]
                              mode = await new Promise(resolve => {
                                const m = Modal.confirm({
                                  title: '这条用例已经有接口场景了',
                                  width: 460,
                                  icon: null,
                                  content: (
                                    <div style={{ fontSize: 13, lineHeight: 1.8 }}>
                                      <div style={{ padding: '8px 12px', background: 'rgba(0,0,0,0.02)', borderRadius: 8, marginBottom: 10 }}>
                                        <b>{head.title}</b>
                                        <div style={{ fontSize: 12, color: '#86909c' }}>
                                          {head.stepCount || 0} 步 · 状态 {head.status}
                                          {list.length > 1 && ` · 该用例下另有 ${list.length - 1} 条（页面只显示步骤最多的这条）`}
                                        </div>
                                      </div>
                                      一个用例只保留一条接口场景。这次生成的 {selectedApis.length} 个接口要怎么处理？
                                      <div style={{ fontSize: 12, color: '#86909c', marginTop: 6 }}>
                                        换掉会把现有步骤删干净，状态回到草稿（内容变了，之前跑通的不算数）。
                                      </div>
                                    </div>
                                  ),
                                  onCancel: () => resolve(null),
                                  // 三选一自己画：默认按钮那套只给得了两个，
                                  // 把第三个藏进正文里等于没给。
                                  footer: () => (
                                    <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8, marginTop: 16 }}>
                                      <Button onClick={() => { resolve(null); m.destroy() }}>取消</Button>
                                      <Button danger onClick={() => { resolve('replace'); m.destroy() }}>换掉现有步骤</Button>
                                      <Button type="primary" style={{ background: '#0ea5a0', borderColor: '#0ea5a0' }}
                                        onClick={() => { resolve('append'); m.destroy() }}>接到后面</Button>
                                    </div>
                                  ),
                                })
                              })
                              if (!mode) return
                            }
                          } catch { /* 查不到就按新建走，后端还有一道 */ }
                          setApiArranging(true)
                          try {
                            const selected = selectedApis.map(idx => debugResult.captured_requests.filter(r => !r.truncated)[idx])
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
                              body: JSON.stringify({ apiInfo, folderName: caseTitle || 'UI流量提取', caseId, onExisting: mode }),
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
                          checked={selectedApis.length === debugResult.captured_requests.filter(r => !r.truncated).length && selectedApis.length > 0}
                          onChange={e => setSelectedApis(e.target.checked ? debugResult.captured_requests.filter(r => !r.truncated).map((_, i) => i) : [])}
                          style={{ cursor: 'pointer' }} />
                      </span>
                      <span style={{ width: 60 }}>方法</span>
                      <span style={{ flex: 1 }}>URL</span>
                      <span style={{ width: 60, textAlign: 'right' }}>状态</span>
                    </div>
                    {debugResult.captured_requests.filter(r => !r.truncated).map((r, i) => {
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
                          <div style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: '#4e5969', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {(r.path || r.url || '').replace(/^https?:\/\/[^/]+/, '')}
                          </div>
                          {reqBody && (
                            <div style={{ fontSize: 10, color: '#86909c', fontFamily: 'var(--font-mono)', marginTop: 2, maxHeight: 40, overflow: 'hidden', textOverflow: 'ellipsis' }}>
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
                          <div style={{ color: '#4e5969', wordBreak: 'break-all', marginBottom: 6, fontFamily: 'var(--font-mono)', fontSize: 11 }}>
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
              ) : null
            )}]),
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
                      ? (finishingMsg
                          ? `${liveSteps.filter(s => s.status === 'passed').length} 步全部完成 · ${finishingMsg}`
                          : `${liveSteps.filter(s => s.status === 'passed').length} 步完成，${liveSteps.filter(s => s.status === 'running').length > 0 ? '1 步执行中' : '等待中...'}`)
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
                    const { name, phase, ms, error } = stepInfo(s, i)
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
                      fontFamily: 'var(--font-mono)', background: '#1e1e2e', color: '#cdd6f4',
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
                          <div style={{ fontSize: 11, color: '#e8453c', fontFamily: 'var(--font-mono)', lineHeight: 1.4, wordBreak: 'break-all' }}>
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
          {/* 原来这儿有个独立的「场景状态」下拉（api/ui_scenario_status）——
              和头部那组三维状态说的是同一件事，两处并排必然出现一处改了另一处没改。
              已删；模板开关直接看该维度的三维状态。 */}
          <Tooltip title={dimStatus === 'completed' ? (isTemplate ? '取消模板' : '标记为模板') : '该维度到「完成」才能标模板'}>
            <Button size="small" type={isTemplate ? 'primary' : 'default'}
              disabled={dimStatus !== 'completed'}
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
              onOk: () => { setScenario(null); setDimStatus?.('draft'); setIsTemplate(false) },
            })
          }}><DeleteOutlined /> 删除</Button>
        </Space>
      </div>

      {/* 步骤视图 */}
      {viewMode === 'steps' && (
        <>
          {type === 'api' ? (
            <ApiStepList steps={steps} onChange={newSteps => updateScenario({ steps: newSteps })} accentColor={accentColor}
                environments={environments} runEnv={runEnv} onEnvChange={onEnvChange} />
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
                    <Input spellCheck={false} value={s.uiTarget || ''} onChange={e => updateStepField(i, 'uiTarget', e.target.value)}
                      placeholder="页面URL或元素选择器" variant="borderless"
                      style={{ flex: 1, fontSize: 12, fontFamily: 'var(--font-mono)', color: accentColor }} />
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
            <div style={{ padding: '8px 14px', fontSize: 12, color: '#e8453c', fontFamily: 'var(--font-mono)' }}>{debugResult.errorSummary}</div>
          )}
          {debugResult.stdout && (
            <pre style={{ margin: 0, padding: 14, fontSize: 11, fontFamily: 'var(--font-mono)', background: '#1e1e2e', color: '#cdd6f4', maxHeight: 300, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
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
                    <span style={{ fontFamily: 'var(--font-mono)' }}>{t.caseCode}</span>
                    {sc?.scriptRefFile && <span style={{ marginLeft: 8, fontFamily: 'var(--font-mono)' }}>{sc.scriptRefFile}</span>}
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
  const location = useLocation()

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
  // 审核标签（NULL = 待提审，不显示）
  const [reviewStatus, setReviewStatus] = useState(null)
  const [manualStatus, setManualStatus] = useState('draft')
  // 这条要做到哪一步（spec/spec_api/full）。CC 靠它决定做几维，
  // 页面上原来根本不显示 —— 于是「UI·草稿」到底是没做还是不做，人分不出来。
  const [targetLevel, setTargetLevel] = useState('spec')
  const [uiStatus, setUiStatus] = useState('draft')
  const [apiStatus, setApiStatus] = useState('draft')
  const [isCore, setIsCore] = useState(false)
  const [flaky, setFlaky] = useState(false)

  // 隔离态直接从 quarantinedUntil 算 —— 到期即失效，不需要谁去清标记
  const quarantined = !!caseData?.quarantinedUntil && new Date(caseData.quarantinedUntil) > new Date()
  // 只读展示：这一栏由 CC 通过 tb_update_case(blocked_external=...) 写，人不在这儿改 ——
  // 它记的是「写不了的原因」，该由干活那边自述，条件到位了也该由它撤。
  const blockedExternal = caseData?.blockedExternal || ''
  // 检测到不稳定 ≠ 被隔离。检测只标记，隔离要人自己点。
  const unstable = !!caseData?.flakyEvidence

  const quarantineCase = async () => {
    try {
      await api.post(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/quarantine`)
      message.success('已隔离，14 天内不进回归；到期自动回来')
      loadData()
    } catch (e) {
      message.error(e?.response?.data?.error?.message || '隔离失败')
    }
  }

  const releaseQuarantine = async () => {
    try {
      await api.post(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/release-quarantine`)
      message.success('已解除隔离，下次执行会正常跑')
      loadData()
    } catch (e) {
      message.error(e?.response?.data?.error?.message || '解除失败')
    }
  }

  // P0 两阶段：确认过「预期结果」这一列，才允许给它挂接口场景和 UI 脚本
  const expectedConfirmed = !!caseData?.expectedConfirmedAt
  const confirmExpected = async () => {
    try {
      await api.post(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/confirm-expected`)
      message.success('已确认预期结果，现在可以补接口场景和 UI 脚本了')
      loadData()
    } catch (e) {
      message.error(e?.response?.data?.error?.message || '确认失败')
    }
  }
  const [preconditions, setPreconditions] = useState('')
  const [expectedResult, setExpectedResult] = useState('')
  const [scriptRefFile, setScriptRefFile] = useState('')
  const [scriptRefFunc, setScriptRefFunc] = useState('')
  const [remark, setRemark] = useState('')
  const [aiReview, setAiReview] = useState(null)
  const [reviewRounds, setReviewRounds] = useState(null)
  const [reviewStatusDerived, setReviewStatusDerived] = useState(null)
  const [aiReviewing, setAiReviewing] = useState(false)
  const [tags, setTags] = useState([])
  const [bugRefs, setBugRefs] = useState([])
  const [steps, setSteps] = useState([{ seq: 1, action: '', expected: '' }])
  const [variablesUsed, setVariablesUsed] = useState([])
  const [newVarInput, setNewVarInput] = useState('')
  const [apiScenario, setApiScenario] = useState(null)
  const [uiScenario, setUiScenario] = useState(null)
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
      api.get(`/projects/${projectId}/environments/${runEnv}/variables`).then(res => {
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
        api.get(`/projects/${projectId}/environments`),
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
        isApiTemplate: c.isApiTemplate || false,
        isUiTemplate: c.isUiTemplate || false,
        lifecycleStatus: c.lifecycleStatus || 'draft',
        targetLevel: c.targetLevel || 'spec',
        manualStatus: c.manualStatus || 'draft',
        uiStatus: c.uiStatus || 'draft',
        apiStatus: c.apiStatus || 'draft',
        reviewStatus: c.reviewStatus || null,
        isCore: c.isCore || false,
        tags: c.tags || [],
        bugRefs: c.bugRefs || [],
      }

      setTitle(vals.title); setType(vals.type); setPriority(vals.priority)
      setModule(vals.module); setSubModule(vals.subModule)
      setAutomationStatus(vals.automationStatus); setFlaky(vals.flaky)
      setLifecycleStatus(vals.lifecycleStatus); setManualStatus(vals.manualStatus)
    setTargetLevel(vals.targetLevel)
      setUiStatus(vals.uiStatus); setApiStatus(vals.apiStatus)
      // 这一行漏掉过：reviewStatus 进了 vals（也就进了 savedRef）和保存体，却没回填 state。
      // 后果不只是审核徽标不显示 —— state 恒为 null 跟 savedRef 里的 'pending' 对不上，
      // 用例一加载完就被判成「有未保存的修改」，点返回必弹确认框。
      // 往 currentSnap 里加字段时，setter 必须跟着加。
      setReviewStatus(vals.reviewStatus)
      setIsCore(vals.isCore)
      setPreconditions(vals.preconditions); setExpectedResult(vals.expectedResult)
      setScriptRefFile(vals.scriptRefFile); setScriptRefFunc(vals.scriptRefFunc)
      setRemark(vals.remark); setSteps(vals.steps); setVariablesUsed(vals.variablesUsed)
      setApiScenario(vals.apiScenario); setUiScenario(vals.uiScenario)
      setIsApiTemplate(vals.isApiTemplate); setIsUiTemplate(vals.isUiTemplate)
      setTags(vals.tags); setBugRefs(vals.bugRefs)

      savedRef.current = JSON.stringify(vals)

      // Check if there's an active script in the scripts table
      try {
        // 用 target_level 判，不用 type。
        // type（api/e2e）的初衷是分「单接口测试」和「场景」，而接口测试模块已下线，
        // 单接口那一类没有实例了；CC 现在拿它当「做不做 UI」用，跟 target_level 重复。
        // 凭 type 判的后果：一条 type=api 的用例补了 UI 脚本，这里仍去取 api 脚本，
        // UI 页签就永远是空的 —— 而 target_level=full 才是"这条要做 UI"的正解。
        const wantsUi = (vals.targetLevel || 'spec') === 'full' || vals.type === 'e2e'
        const scriptRes = await api.get(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/active?type=${wantsUi ? 'ui' : 'api'}`)
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
            const varRes = await api.get(`/projects/${projectId}/environments/${activeEnvId}/variables`)
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
    isApiTemplate, isUiTemplate,
    lifecycleStatus, manualStatus, uiStatus, apiStatus, reviewStatus, isCore, targetLevel,
    tags, bugRefs,
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
      // 不传 type —— 用例的 type 是 api/e2e，脚本的 type 是 api/ui，两者不是一回事。
      // 原先直接把用例 type 当脚本 type 传，e2e 用例永远查不到，api 用例也看不到自己的 UI 执行。
      const res = await api.get(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/runs`)
      setScriptRuns(res.data || [])
    } catch { setScriptRuns([]) }
    finally { setScriptRunsLoading(false) }
  }

  useEffect(() => {
    const handler = (e) => { if (isDirty) { e.preventDefault(); e.returnValue = '' } }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [isDirty])

  // location.key === 'default' 表示这条详情就是本次会话的第一条记录（直接开链接、
  // 或刷新过），此时 navigate(-1) 会退出应用或原地不动 —— 按钮看着像点了没反应。
  // 这种情况直接回列表。
  const goBack = () => {
    if (location.key === 'default') navigate(`/projects/${projectId}/cases`)
    else navigate(-1)
  }

  const handleBack = () => {
    if (isDirty) {
      Modal.confirm({
        title: '未保存的修改', content: '当前有未保存的修改，确定离开吗？',
        okText: '离开', cancelText: '继续编辑', onOk: goBack,
      })
    } else goBack()
  }

  const addStep = () => setSteps(prev => [...prev, { seq: prev.length + 1, action: '', expected: '' }])
  const removeStep = (idx) => setSteps(prev => prev.filter((_, i) => i !== idx).map((s, i) => ({ ...s, seq: i + 1 })))
  const updateStep = (idx, field, value) => setSteps(prev => prev.map((s, i) => i === idx ? { ...s, [field]: value } : s))

  // 审核历史。**审核不是一个当前值，是一条链**：AI 打回 → CC 整改 → 再审 → 通过。
  // 没有这条链，「跟进到哪了」只能靠人记。
  const loadReviewRounds = useCallback(async () => {
    if (!branchId || !caseId) return
    try {
      const r = await api.get(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/review-rounds`)
      setReviewRounds(r.data?.rounds || [])
      setReviewStatusDerived(r.data?.status || null)
    } catch { /* request.js 已提示 */ }
  }, [projectId, branchId, caseId])

  useEffect(() => { loadReviewRounds() }, [loadReviewRounds])

  const handleSave = async () => {
    try {
      await api.put(`/projects/${projectId}/branches/${branchId}/cases/${caseId}`, {
        title, type, priority, module, subModule, automationStatus,
        isFlaky: flaky, preconditions, expectedResult, scriptRefFile, scriptRefFunc,
        remark, steps, variablesUsed, apiScenario, uiScenario,
        isApiTemplate, isUiTemplate,
        lifecycleStatus, manualStatus, uiStatus, apiStatus, reviewStatus, isCore, targetLevel,
        tags, bugRefs,
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
        {/* 箭头和「用例管理」合成一个按钮：原先箭头是 24×24 的独立小按钮，
            旁边的文字不可点，实际很难瞄准。 */}
        <Button type="text" icon={<ArrowLeftOutlined />} onClick={handleBack}
          style={{ height: 32, padding: '0 10px 0 8px', color: '#86909c', fontSize: 12 }}>
          用例管理
        </Button>
        <span style={{ color: 'rgba(0,0,0,0.15)', fontSize: 12 }}>/</span>
        <span style={{ fontSize: 12, color: '#86909c', fontFamily: 'var(--font-mono)' }}>{caseCode}</span>
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
          {/* 这一组和下面原来那组「场景覆盖指示器」说的是同一件事的两半：
              一个说"什么状态"、一个说"有没有内容"，而"有没有"是"什么状态"的子集。
              两组并排 = 同一件事说两遍，实测出现过一组说有、另一组说未开始。
              合成这一组：状态词用和列表页一样的三档，括号里带内容量。 */}
          {/* 覆盖层级 —— 决定下面三维里哪几维算数。原来页面上完全不显示它，
              于是「UI·草稿」是没做还是不做，只能靠猜。 */}
          <InlineProp value={`计划·${TARGET_LEVEL[targetLevel]?.label || targetLevel}`}
            color="#7c5cff" bg="rgba(124,92,255,0.10)">
            <DropdownList activeKey={targetLevel} onSelect={setTargetLevel}
              items={Object.entries(TARGET_LEVEL).map(([k, v]) => ({
                key: k, label: `${v.label} —— ${v.hint}`, dot: 'circle', color: '#7c5cff' }))} />
          </InlineProp>
          {[['手动', 'manual', manualStatus, setManualStatus, steps.length],
            ['UI', 'ui', uiStatus, setUiStatus, (uiScenario?.steps?.length || uiScenario?.lastResults?.length || 0)],
            ['接口', 'api', apiStatus, setApiStatus, (apiScenario?.steps?.length || 0)]].map(([lbl, dim, val, setter, n]) => {
            // 文字和颜色都取自同一个档位对象 —— 原来文字来自三档、颜色来自六态，
            // 于是「调试中」会出现两种颜色，同一屏自相矛盾。
            const b = dimBadge(targetLevel, dim, val)
            return (
            <InlineProp key={lbl} value={`${lbl}·${b.label}${n ? ` (${n})` : ''}`}
              color={b.color} bg={b.bg}>
              <DropdownList activeKey={val} onSelect={setter}
                items={DIM_STATUS_KEYS.map(s => ({ key: s, label: dimStatusMap[s].label, dot: 'circle', color: dimStatusMap[s].color }))} />
            </InlineProp>
          )})}
          {/* 「卡在外部条件上」：CC 自述等什么。看板上「没人写」和「写不了」原来长得
              一模一样，每轮都要人挨个去问一遍。它不是状态、不免检任何阻塞，只是归责。 */}
          {blockedExternal && (
            <InlineProp icon={<WarningOutlined />} value={`等外部·${blockedExternal}`}
              color="#fa8c16" bg="#fff7e6" />
          )}
          {/* 关联 bug：人和 CC 都能写。**标 fixed 不等于关系解除** ——
              解除由「重跑绿了」这个执行事实决定，平台自动摘；这里只表达
              「据说修好了，该重跑一遍」。 */}
          {/* 单条 AI 评审：详情页才是"改完立刻复核"的地方 —— 列表那个批量入口
              适合验收一批，不适合边改边看。 */}
          <InlineProp icon={<SearchOutlined />}
            value={aiReview ? `AI ${aiReview.verdict === 'approved' ? '通过' : '打回'}·${aiReview.total}`
              : caseData.qualityScore?.total != null ? `AI 评分 ${caseData.qualityScore.total}` : 'AI 审核'}
            color={aiReview ? (aiReview.verdict === 'approved' ? '#0ea5a0' : '#e8453c') : '#86909c'}
            bg={aiReview ? (aiReview.verdict === 'approved' ? '#f6ffed' : '#fff1f0') : 'rgba(0,0,0,0.02)'}>
            <div style={{ minWidth: 380 }}>
              <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                <Button size="small" type="primary" loading={aiReviewing}
                  onClick={async () => {
                    setAiReviewing(true)
                    try {
                      const r = await api.post(
                        `/projects/${projectId}/branches/${branchId}/cases/${caseId}/ai-review`)
                      setAiReview(r.data); loadData()
                    } catch (e) {
                      message.error(e?.response?.data?.error?.message || '评审失败')
                    } finally { setAiReviewing(false) }
                  }}>评审这一条</Button>
                <Button size="small" loading={aiReviewing}
                  onClick={async () => {
                    setAiReviewing(true)
                    try {
                      const r = await api.post(
                        `/projects/${projectId}/branches/${branchId}/cases/${caseId}/ai-review?runFirst=true`)
                      setAiReview(r.data); loadData()
                    } catch (e) {
                      message.error(e?.response?.data?.error?.message || '评审失败')
                    } finally { setAiReviewing(false) }
                  }}>先跑一遍再评</Button>
              </div>
              {aiReview ? (
                <div style={{ fontSize: 12, lineHeight: 1.7 }}>
                  <div style={{ marginBottom: 6 }}>
                    {/* **这次是真跑过还是静态看的，必须露出来。** 两者结论强度差一个量级：
                        同一条实测静态 84 分通过、真跑 56 分打回（接口场景用的端点页面
                        一次都没调）。此前「先跑一遍再评」跑不成时只留一条 minor finding，
                        页面上两种审核长得一模一样。 */}
                    <Tag color={aiReview.reviewMode === 'run_first' ? 'cyan' : 'default'}
                      style={{ marginRight: 6 }}>
                      {aiReview.reviewMode === 'run_first'
                        ? `执行式审核 · 对了 ${aiReview.trafficSeen ?? 0} 条真实流量`
                        : '静态审核 · 没有真跑'}
                    </Tag>
                    <b>{aiReview.verdictReason}</b>
                    {aiReview.summary ? <span style={{ color: '#4e5969' }}> · {aiReview.summary}</span> : null}
                    {aiReview.reviewModeNote ? (
                      <div style={{ color: '#faad14', marginTop: 4 }}>{aiReview.reviewModeNote}</div>
                    ) : null}
                  </div>
                  {Object.entries(aiReview.dimensions || {}).map(([k, d]) => (
                    <div key={k} style={{ display: 'flex', justifyContent: 'space-between' }}>
                      <span style={{ color: '#86909c' }}>{d.label}（{d.weight}%）</span>
                      <span style={{ color: d.score >= 80 ? '#0ea5a0' : d.score >= 60 ? '#faad14' : '#e8453c' }}>
                        {d.score}</span>
                    </div>
                  ))}
                  {(aiReview.findings || []).filter(f => f.severity !== 'minor').map((f, i) => (
                    <div key={i} style={{ marginTop: 6 }}>
                      <Tag color={f.severity === 'blocker' ? 'error' : 'warning'}
                        style={{ fontSize: 10, margin: '0 4px 0 0' }}>
                        {f.severity === 'blocker' ? '致命' : '重要'}</Tag>
                      <span style={{ color: '#86909c' }}>{f.where}</span>：{f.problem}
                      {f.fix && <span style={{ color: '#0ea5a0' }}> → {f.fix}</span>}
                    </div>
                  ))}
                </div>
              ) : (
                <div style={{ fontSize: 11, color: '#86909c', lineHeight: 1.7 }}>
                  六维：场景合理性 / 验证点到位 / 接口必要性 / UI 脚本 / 覆盖遗漏 / 纪律。<br />
                  有致命问题一律打回（分数线 80）。「先跑一遍再评」会真跑这条的接口场景 ——
                  断言咬不咬得住静态看不出来。
                </div>
              )}
            </div>
          </InlineProp>
{/* 回推四问的答案。**只读** —— 这是干活那边的自证（它有需求和代码），
              人要补充意见写备注。没答的话审核会按「自证不全」扣分，所以这里要看得见。 */}
          <InlineProp icon={<FlagOutlined />}
            value={caseData.reflectionPending ? '待自证' : '已自证'}
            color={caseData.reflectionPending ? '#fa8c16' : '#0ea5a0'}
            bg={caseData.reflectionPending ? '#fff7e6' : '#f6ffed'}>
            <div style={{ minWidth: 400, fontSize: 12, lineHeight: 1.75 }}>
              {caseData.reflectionPending && (
                <div style={{ color: '#fa8c16', marginBottom: 8 }}>
                  回推时的四个场景级反问还没答完。规则判不了这四件，只有干活的人答得上；
                  没答的话 AI 审核会按「自证不全」扣分，这条也算不上可交付。
                </div>
              )}
              {[['verificationPoints', '哪几条断言在验标题承诺的事'],
                ['clarity', '是不是只验一件事'],
                ['coverage', '和邻居不重复在哪 / 本模块还缺什么'],
                ['expectationSource', '预期按需求还是按实测写的']].map(([k, label]) => (
                <div key={k} style={{ marginBottom: 8 }}>
                  <div style={{ color: '#86909c' }}>{label}</div>
                  <div>{caseData.reflections?.[k] || <span style={{ color: '#c9cdd4' }}>—— 没答</span>}</div>
                </div>
              ))}
              {caseData.reflections?.answeredAt && (
                <div style={{ color: '#86909c' }}>
                  {caseData.reflections.by} · {String(caseData.reflections.answeredAt).slice(0, 16).replace('T', ' ')}
                </div>
              )}
            </div>
          </InlineProp>
                    <InlineProp icon={<BugOutlined />}
            value={bugRefs.some(r => (r.status || 'open') === 'open')
              ? `卡 bug·${bugRefs.filter(r => (r.status || 'open') === 'open').length}`
              : bugRefs.length ? `抓到过 bug·${bugRefs.length}` : '无关联bug'}
            color={bugRefs.some(r => (r.status || 'open') === 'open') ? '#e8453c'
              : bugRefs.length ? '#4e5969' : '#86909c'}
            bg={bugRefs.some(r => (r.status || 'open') === 'open') ? '#fff1f0'
              : 'rgba(0,0,0,0.02)'}>
            <div style={{ minWidth: 340 }}>
              {bugRefs.map((r, i) => (
                <div key={i} style={{ display: 'flex', gap: 6, alignItems: 'center', marginBottom: 6 }}>
                  <Input size="small" value={r.ref} placeholder="单号或一句话"
                    onChange={e => setBugRefs(prev => prev.map((x, j) => j === i ? { ...x, ref: e.target.value } : x))} />
                  <Select size="small" value={r.status || 'open'} style={{ width: 108, flexShrink: 0 }}
                    onChange={v => setBugRefs(prev => prev.map((x, j) => j === i ? { ...x, status: v } : x))}
                    options={[{ value: 'open', label: '还没验回来' }, { value: 'fixed', label: '已修复' }]} />
                  <Button size="small" type="text" danger icon={<DeleteOutlined />}
                    onClick={() => setBugRefs(prev => prev.filter((_, j) => j !== i))} />
                </div>
              ))}
              <Button size="small" type="dashed" block
                onClick={() => setBugRefs(prev => [...prev, { ref: '', status: 'open' }])}>+ 关联一个 bug</Button>
              {/* 「删」那个按钮很容易被当成正常的收尾动作用 —— 说清楚它不是。 */}
              <div style={{ fontSize: 11, color: '#86909c', marginTop: 8, lineHeight: 1.7 }}>
                「还没验回来」= 批量回归跳过这条，也不计入通过率。<br />
                bug 关闭后回来调通，改成「已修复」—— <b>记录永久保留</b>，
                这条用例曾经抓到过 bug 是它的价值证明。<br />
                删除只用于关联错了，不是正常的收尾方式。
              </div>
            </div>
          </InlineProp>
          <InlineProp icon={<TagsOutlined />}
            value={tags.length ? tags.join('、').slice(0, 18) : '无标签'}
            color={tags.length ? '#4e5969' : '#86909c'}>
            <div style={{ minWidth: 280 }}>
              <Select mode="tags" size="small" value={tags} onChange={setTags}
                style={{ width: '100%' }} placeholder="回车添加，如：冒烟 / 需要真数据"
                tokenSeparators={[',', '，', ' ']} open={false} />
              <div style={{ fontSize: 11, color: '#86909c', marginTop: 8 }}>
                只用来筛，不表达状态和审核结论。最多 20 个、每个 32 字内。
              </div>
            </div>
          </InlineProp>
          <InlineProp icon={<WarningOutlined />}
            value={quarantined ? '已隔离' : unstable ? '不稳定' : flaky ? 'Flaky' : '正常'}
            color={quarantined ? '#e8453c' : unstable ? '#fa8c16' : flaky ? '#faad14' : '#86909c'}
            bg={quarantined ? '#fff1f0' : unstable ? '#fff7e6' : flaky ? '#fffbe6' : 'rgba(0,0,0,0.02)'}>
            <div style={{ padding: '4px 8px', minWidth: 300 }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 12 }}>
                <span style={{ fontSize: 13 }}>Flaky 标记（人工）</span>
                <Switch size="small" checked={flaky} onChange={v => setFlaky(v)} />
              </div>
              {/* 自动隔离要能复核：凭什么说它 flaky、什么时候回来、怎么提前放出来。
                  只给一个红标不给依据的话，等于平台单方面判了一条用例不跑。 */}
              {/* 检测到不稳定 —— **不跳过它**，只标出来 + 给"该往哪儿看"。
                  时好时坏本身就是信息（时序/脏数据/并发/环境），自动隔离等于自动
                  把问题藏起来。跳不跳由人定，下面那个按钮才会隔离。 */}
              {unstable && (
                <div style={{ marginTop: 8, paddingTop: 8, borderTop: '1px solid rgba(0,0,0,0.06)' }}>
                  <div style={{ fontSize: 12.5, color: quarantined ? '#e8453c' : '#fa8c16', fontWeight: 600, marginBottom: 4 }}>
                    {quarantined ? '已隔离，执行时会被跳过' : '检测到不稳定 —— 仍会照常执行'}
                  </div>
                  <div style={{ fontSize: 12, color: '#4e5969', lineHeight: 1.8 }}>
                    {caseData.flakyEvidence?.note || '结果反复翻转'}
                    {quarantined && (
                      <>
                        <br />
                        到期：{new Date(caseData.quarantinedUntil).toLocaleString('zh-CN')}（到期自动恢复）
                      </>
                    )}
                  </div>

                  {/* 平台判不出根因，但能把失败之间的共性/差异摆出来 —— 这是查的起点 */}
                  {caseData.flakyEvidence?.diagnosis && (
                    <div style={{ marginTop: 8, padding: '8px 10px', borderRadius: 8,
                      background: 'rgba(250,140,22,0.06)', border: '1px solid rgba(250,140,22,0.2)',
                      fontSize: 12, color: '#4e5969', lineHeight: 1.8 }}>
                      <div style={{ fontWeight: 600, marginBottom: 2 }}>该往哪儿看</div>
                      {caseData.flakyEvidence.diagnosis.hint}
                      {Object.keys(caseData.flakyEvidence.diagnosis.phenomena || {}).length > 0 && (
                        <div style={{ marginTop: 2 }}>
                          现象：{Object.entries(caseData.flakyEvidence.diagnosis.phenomena)
                            .map(([k, v]) => `${phenomenonLabel(k)} ×${v}`).join('、')}
                        </div>
                      )}
                      {caseData.flakyEvidence.diagnosis.compare?.lastPassed
                        && caseData.flakyEvidence.diagnosis.compare?.lastFailed && (
                        <div style={{ marginTop: 2 }}>
                          把最近一次成功和最近一次失败的截图/流量摆一起对比，最快看出差别
                          （见「执行历史」页签）
                        </div>
                      )}
                    </div>
                  )}

                  {(caseData.flakyEvidence?.runs || []).length > 0 && (
                    <div style={{ marginTop: 6 }}>
                      {caseData.flakyEvidence.runs.map((r, i) => (
                        <div key={i} style={{ fontSize: 11.5, color: '#86909c', fontFamily: 'var(--font-mono)' }}>
                          {r.status === 'passed' ? '✓' : '✗'} {(r.at || '').slice(0, 16).replace('T', ' ')}
                          {r.error ? ` — ${String(r.error).slice(0, 40)}` : ''}
                        </div>
                      ))}
                    </div>
                  )}

                  {quarantined ? (
                    <Button size="small" style={{ marginTop: 8 }} onClick={releaseQuarantine}>
                      解除隔离（问题已解决）
                    </Button>
                  ) : (
                    <Button size="small" style={{ marginTop: 8 }} onClick={quarantineCase}>
                      先隔离它（14 天，我知道它不稳）
                    </Button>
                  )}
                </div>
              )}
            </div>
          </InlineProp>
          {/* 审核标签。三维全完成才有意义 —— 没到的时候不显示（NULL=待提审）。 */}
          {/* AI 判的要标明是 AI —— 列表里已经是「AI 过/AI 打回」，详情还写「已审」的话，
              同一条用例两处口径不一致，人不知道该不该复核 */}
          {reviewStatus && (
            <InlineProp icon={<CheckCircleOutlined />}
              value={`审核·${caseData.qualityScore?.by === 'ai'
                ? {pending: '待审', approved: 'AI 通过', rejected: 'AI 打回'}[reviewStatus] || REVIEW[reviewStatus]?.label
                : REVIEW[reviewStatus]?.label || reviewStatus}`}
              color={REVIEW[reviewStatus]?.color} bg={REVIEW[reviewStatus]?.bg}>
              <div style={{ padding: '4px 8px', minWidth: 260, lineHeight: 1.8 }}>
                <div style={{ fontSize: 12, color: '#4e5969' }}>
                  三维都完成后自动进「待审」。<b>审核不挡回归</b> —— 不审也能建计划直接跑。
                </div>
                <DropdownList activeKey={reviewStatus} onSelect={setReviewStatus}
                  items={REVIEW_KEYS.map(k => ({ key: k, label: REVIEW[k].label,
                    dot: 'circle', color: REVIEW[k].color }))} />
              </div>
            </InlineProp>
          )}
          {/* P0 才显示。挂接口/UI 之前必须有人过一遍「预期结果」这一列 ——
              三份产物同源生成必然互相一致，而一致会被当成已经验证过。
              这个标不显示的话，CC 回推被门禁拦住时，人在页面上根本找不到怎么解。 */}
          {priority === 'P0' && (
            <InlineProp icon={<CheckCircleOutlined />}
              value={expectedConfirmed ? '预期已确认' : '预期待确认'}
              color={expectedConfirmed ? '#0ea5a0' : '#fa8c16'}
              bg={expectedConfirmed ? '#e0f7f6' : '#fff7e6'}>
              <div style={{ padding: '4px 8px', minWidth: 320, lineHeight: 1.8 }}>
                {expectedConfirmed ? (
                  <>
                    <div style={{ fontSize: 12.5, color: '#0ea5a0', fontWeight: 600 }}>
                      已确认预期结果
                    </div>
                    <div style={{ fontSize: 12, color: '#4e5969' }}>
                      {caseData.expectedConfirmedActor ? `由 ${caseData.expectedConfirmedActor} 确认 · ` : ''}
                      {new Date(caseData.expectedConfirmedAt).toLocaleString('zh-CN')}
                      {caseData.expectedConfirmedNote && (
                        <div style={{ marginTop: 6, padding: '6px 10px', borderRadius: 8,
                          background: 'rgba(14,165,160,0.06)', border: '1px solid rgba(14,165,160,0.18)' }}>
                          确认内容：{caseData.expectedConfirmedNote}
                        </div>
                      )}
                      <div style={{ marginTop: 6 }}>
                        改动步骤或预期结果会让这次确认自动失效 —— 确认的是当时那一版。
                      </div>
                    </div>
                  </>
                ) : (
                  <>
                    <div style={{ fontSize: 12.5, color: '#fa8c16', fontWeight: 600 }}>
                      还没有人确认过预期结果
                    </div>
                    <div style={{ fontSize: 12, color: '#4e5969' }}>
                      不挡任何操作 —— 接口场景和 UI 脚本照样能挂。只是同源生成的三份产物
                      容易互相一致而不正确（把「创建成功」做成「返回 200」），
                      有人过一眼「预期结果」这一列会稳得多。
                      <br />
                      CC 在对话里跟你确认过的话，它会把确认内容一起带上来，这里直接显示。
                    </div>
                    <Button size="small" type="primary" ghost style={{ marginTop: 8 }} onClick={confirmExpected}>
                      确认预期结果
                    </Button>
                  </>
                )}
              </div>
            </InlineProp>
          )}
          <ReadonlyProp label="来源" value={caseData.source || 'manual'} />
          {/* 「评分」这一栏删了 —— 上面「AI 评分 N」已经有同一个数，
              两处显示同一个值，人会以为是两回事 */}

          {/* 「场景覆盖指示器」已并进上面那组三维状态 —— 它只说"有没有"，
              而那是"什么状态"的子集，并排显示是把同一件事说两遍。 */}
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
                        style={{ fontFamily: 'var(--font-mono)', fontSize: 11, background: '#edf3ff', border: '1px solid rgba(78,138,240,0.3)', color: '#4e8af0', borderRadius: 12, padding: '1px 6px' }}>
                        {v}
                      </Tag>
                    ))}
                    {variablesUsed.length === 0 && <span style={{ fontSize: 12, color: '#c9cdd4' }}>暂无</span>}
                  </div>
                  <div style={{ display: 'flex', gap: 4 }}>
                    <Input spellCheck={false} value={newVarInput} onChange={e => setNewVarInput(e.target.value)} size="small"
                      placeholder="参数名" style={{ flex: 1, fontFamily: 'var(--font-mono)', fontSize: 11 }}
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
                    dimStatus={apiStatus} setDimStatus={setApiStatus}
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
                dimStatus={uiStatus} setDimStatus={setUiStatus}
                isTemplate={isUiTemplate} setIsTemplate={setIsUiTemplate}
                type="e2e" accentColor="#7c5cbf"
                onImportTemplate={() => { setTemplateModalType('ui'); setTemplateModalOpen(true) }}
                manualSteps={steps} caseTitle={title}
                projectId={projectId} branchId={branchId} caseId={caseId}
                environments={environments} runEnv={runEnv} onEnvChange={setRunEnv}
                onScriptSaved={() => setHasActiveScript(true)}
              />
            )},

            { key: 'review', label: <span><CheckCircleOutlined style={{ marginRight: 4 }} />审核{reviewRounds?.length ? <span style={{ fontSize: 11, color: '#86909c', marginLeft: 4 }}>({reviewRounds.length} 轮)</span> : null}</span>, children: (
              <Card styles={{ body: { padding: '16px 24px' } }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14 }}>
                  <Tag color={{approved: 'success', rejected: 'error', resubmitted: 'warning', pending: 'processing'}[reviewStatusDerived] || undefined}>
                    {{approved: '通过', rejected: '打回', resubmitted: '整改待复审',
                      pending: '待审', not_submitted: '待提审'}[reviewStatusDerived] || '待提审'}
                  </Tag>
                  <Button size="small" type="primary" loading={aiReviewing} onClick={async () => {
                    setAiReviewing(true)
                    try {
                      await api.post(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/ai-review`)
                      await loadReviewRounds(); loadData()
                    } catch (e) { message.error(e?.response?.data?.error?.message || '审核失败') }
                    finally { setAiReviewing(false) }
                  }}>AI 审核这一条</Button>
                  <Button size="small" loading={aiReviewing} onClick={async () => {
                    setAiReviewing(true)
                    try {
                      await api.post(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/ai-review?runFirst=true`)
                      await loadReviewRounds(); loadData()
                    } catch (e) { message.error(e?.response?.data?.error?.message || '审核失败') }
                    finally { setAiReviewing(false) }
                  }}>先跑一遍再审</Button>
                  <span style={{ flex: 1 }} />
                  {/* 人工覆盖：记成一轮，不悄悄改状态 —— 人推翻机器的判断本身就是信息 */}
                  <Button size="small" onClick={async () => {
                    try {
                      await api.post(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/review-override?verdict=approved&reason=${encodeURIComponent('人工判定通过')}`)
                      await loadReviewRounds(); loadData()
                    } catch { /* */ }
                  }}>人工通过</Button>
                  <Button size="small" danger onClick={async () => {
                    try {
                      await api.post(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/review-override?verdict=rejected&reason=${encodeURIComponent('人工打回')}`)
                      await loadReviewRounds(); loadData()
                    } catch { /* */ }
                  }}>人工打回</Button>
                </div>
                {!reviewRounds?.length && (
                  <Empty description="还没审过。点上面「AI 审核这一条」" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                )}
                {/* 时间线：最新在上。每轮是什么、谁做的、必改哪几条，一眼看完 */}
                {(reviewRounds || []).map(r => (
                  <div key={r.round} style={{ display: 'flex', gap: 12, padding: '12px 0',
                    borderTop: '1px solid rgba(0,0,0,0.04)' }}>
                    <div style={{ width: 96, flexShrink: 0, fontSize: 12, color: '#86909c' }}>
                      第 {r.round} 轮<br />
                      {r.at ? String(r.at).slice(5, 16).replace('T', ' ') : ''}
                    </div>
                    <div style={{ flex: 1, fontSize: 13 }}>
                      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 4 }}>
                        <Tag style={{ margin: 0 }} color={
                          r.kind === 'cc_resubmit' ? 'warning'
                          : r.verdict === 'approved' ? 'success'
                          : r.verdict === 'rejected' ? 'error' : undefined}>
                          {r.kind === 'cc_resubmit' ? 'CC 提交整改'
                            : r.kind === 'human_override' ? '人工判定'
                            : r.verdict === 'approved' ? 'AI 通过' : 'AI 打回'}
                        </Tag>
                        {r.total != null && <b>{r.total} 分</b>}
                        {/* **这轮是真跑过还是静态看的。** 两者结论强度差一个量级：
                            同一条实测静态 84 分通过、真跑 56 分打回（接口场景用的端点
                            页面一次都没调过，判据是 URL 集合比对、不靠模型）。
                            此前两种在这条时间线上长得一模一样，"凭什么过的"看不出来。
                            老轮次没有这个字段，不编造，不显示。 */}
                        {r.kind === 'ai_review' && r.reviewMode ? (
                          <Tooltip title={r.reviewMode === 'run_first'
                            ? `审之前真跑了一遍，拿这次的 ${r.trafficSeen ?? 0} 条真实流量和接口场景对了账`
                            : '只看了定义没有真跑 —— 「接口场景用的端点页面到底调不调」这类问题静态看不出来'}>
                            <Tag style={{ margin: 0 }}
                              color={r.reviewMode === 'run_first' ? 'cyan' : undefined}>
                              {r.reviewMode === 'run_first'
                                ? `执行式 · 流量 ${r.trafficSeen ?? 0}` : '静态'}
                            </Tag>
                          </Tooltip>
                        ) : null}
                        <span style={{ color: '#86909c', fontSize: 12 }}>
                          {r.actor}{r.model ? ` · ${r.model}` : ''}
                        </span>
                      </div>
                      {r.dimensions && (
                        <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>
                          {Object.entries(r.dimensions).map(([k, v]) => `${DIM_LABEL[k] || k} ${v}`).join(' / ')}
                        </div>
                      )}
                      {r.summary && <div style={{ color: '#4e5969', marginBottom: 4 }}>{r.summary}</div>}
                      {(r.findings || []).filter(f => f.severity !== 'minor').map((f, i) => (
                        <div key={i} style={{ fontSize: 12, marginTop: 3, lineHeight: 1.7 }}>
                          <Tag color={f.severity === 'blocker' ? 'error' : 'warning'}
                            style={{ fontSize: 10, margin: '0 6px 0 0' }}>
                            {f.severity === 'blocker' ? '致命' : '重要'}</Tag>
                          <span style={{ color: '#86909c' }}>{f.where}</span>：{f.problem}
                          {f.fix && <span style={{ color: '#0ea5a0' }}> → {f.fix}</span>}
                        </div>
                      ))}
                      {r.changed && (
                        <div style={{ fontSize: 12, color: '#4e5969' }}>
                          {r.changed.note}（步骤 {r.changed.stepCount} 步
                          {r.changed.pendingFindings ? `，上轮有 ${r.changed.pendingFindings} 条待改` : ''}）
                        </div>
                      )}
                      {(r.coverageGaps || []).length > 0 && (
                        <div style={{ fontSize: 12, color: '#86909c', marginTop: 4 }}>
                          覆盖情报：{r.coverageGaps.join('；')}
                        </div>
                      )}
                    </div>
                  </div>
                ))}
              </Card>
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
                      <RunDetail run={r} projectId={projectId} branchId={branchId}
                        caseId={caseId} onConfirmed={loadScriptRuns} />
                    ),
                    rowExpandable: () => true,
                  }}
                  columns={[
                    {
                      title: '时间', dataIndex: 'createdAt', width: 170,
                      render: v => v ? new Date(v).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' }) : '-'
                    },
                    {
                      title: '类型', dataIndex: 'scriptType', width: 70,
                      render: v => <Tag style={{ margin: 0, fontSize: 11 }} color={v === 'ui' ? 'purple' : 'cyan'}>{v === 'ui' ? 'UI' : '接口'}</Tag>
                    },
                    {
                      // 回归=计划/批量跑，算通过率；调试=即席跑，不算
                      title: '模式', dataIndex: 'runMode', width: 90,
                      render: (v, row) => (
                        <span style={{ fontSize: 12, color: v === 'regression' ? '#0ea5a0' : '#86909c' }}>
                          {v === 'regression' ? '回归' : '调试'}
                          {row.attempt > 1 && <span style={{ marginLeft: 4, color: '#fa8c16' }}>第{row.attempt}次</span>}
                        </span>
                      )
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
                      render: v => v ? <span style={{ color: '#e8453c', fontFamily: 'var(--font-mono)', fontSize: 12 }}>{v}</span> : '-'
                    },
                  ]}
                />
              </Card>
            )},

            // 溯源只有走「AI 生成用例」流水线的用例才有（实测 49/257）。
            // 常驻的话，八成用例点进去是两行"没有关联需求点"的占位 ——
            // 有内容才给页签，没有就不占位置。
            ...(!(caseData?.requirementPointIds?.length || caseData?.generationTaskId) ? [] : [
              { key: 'provenance', label: '来源', children: <ProvenanceTab caseId={caseId} /> },
            ]),
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
            setApiStatus('draft')
          } else {
            setUiScenario(sc)
            setUiStatus('draft')
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
            <div style={{ fontSize: 12, color: '#86909c', fontFamily: 'var(--font-mono)' }}>{caseCode}</div>
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
                  脚本: <span style={{ fontFamily: 'var(--font-mono)', color: '#4e5969' }}>{scriptRefFile}</span>
                </div>
              )}
              <div style={{ textAlign: 'center', marginBottom: runResult ? 16 : 0 }}>
                <Button type="primary" loading={runStatus === 'running'} disabled={!runEnv}
                  onClick={async () => {
                    if (!runEnv) { message.warning('请先选择执行环境'); return }
                    setRunStatus('running'); setRunResult(null)
                    try {
                      const res = await api.post(`/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts/run?type=${type === 'e2e' ? 'ui' : 'api'}`, { envId: runEnv })
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
                      <pre style={{ margin: 0, fontSize: 12, color: '#434343', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 150, overflow: 'auto' }}>{runResult.errorSummary}</pre>
                    </div>
                  )}

                  {runResult.stdout && (
                    <details>
                      <summary style={{ cursor: 'pointer', fontSize: 12, color: '#86909c', marginBottom: 6, userSelect: 'none' }}>执行日志</summary>
                      <pre style={{ margin: 0, padding: 12, background: '#1e1e1e', color: '#d4d4d4', borderRadius: 12, fontSize: 11, fontFamily: 'var(--font-mono)', maxHeight: 250, overflow: 'auto', whiteSpace: 'pre-wrap' }}>{runResult.stdout}</pre>
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



// 这条用例从哪来：关联的需求点（带原文引用）+ 生成事件时间线。
// 替代原来的「需求溯源」和「生成档案」——
//   需求溯源只渲染一个裸编号 "R3"，而 requirement_points 里有标题、原文引用和
//   字符偏移锚点，107 条数据一直在，只是最后一公里没接；
//   生成档案是一句写死的占位文案（"xxx 事件将在此展示"），而 case_gen_events
//   里有 49 条真事件。产品里出现开发者写给自己的备忘录，是最直接的可信度损失。
const GEN_EVENT_LABEL = {
  generated: { label: '生成', color: 'blue' },
  scored: { label: '评分', color: 'purple' },
  reviewed: { label: '评审', color: 'cyan' },
  rejected: { label: '打回', color: 'red' },
  regenerated: { label: '重生成', color: 'orange' },
}

function ProvenanceTab({ caseId }) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!caseId) return
    setLoading(true)
    api.get(`/cases/${caseId}/provenance`)
      .then(res => setData(res.data)).catch(() => {}).finally(() => setLoading(false))
  }, [caseId])

  if (loading) return <div style={{ textAlign: 'center', padding: 40 }}><Spin /></div>
  if (!data) return <Empty description="加载失败" image={Empty.PRESENTED_IMAGE_SIMPLE} />

  const pts = data.requirementPoints || []
  const evs = data.events || []

  return (
    <Card styles={{ body: { padding: '16px 24px' } }}>
      <div style={{ marginBottom: 18 }}>
        <h4 style={{ fontSize: 13, color: '#86909c', marginBottom: 8 }}>
          关联需求点 {pts.length > 0 && <span style={{ color: '#c9cdd4' }}>（{pts.length}）</span>}
        </h4>
        {pts.length === 0 ? (
          <span style={{ fontSize: 12, color: '#c9cdd4' }}>
            没有关联需求点。用「AI 生成用例」从需求文档产出的用例才会带需求点。
          </span>
        ) : pts.map(p => (
          <div key={p.code} style={{
            marginBottom: 10, padding: '10px 12px', borderRadius: 10,
            background: p.missing ? '#fff2f0' : 'rgba(0,0,0,0.02)',
            border: p.missing ? '1px solid #ffccc7' : '1px solid rgba(0,0,0,0.04)',
          }}>
            <Space size={8} style={{ marginBottom: p.quoteText ? 6 : 0 }}>
              <Tag color={p.missing ? 'error' : 'blue'} style={{ margin: 0, fontFamily: 'var(--font-mono)' }}>{p.code}</Tag>
              <span style={{ fontSize: 13, fontWeight: 500 }}>
                {p.title || '需求点已不存在'}
              </span>
              {p.missing && (
                <Tooltip title="用例上记着这个编号，但需求文档里已经查不到它 —— 需求可能改过，这条用例也许该更新">
                  <Tag color="error" style={{ margin: 0, fontSize: 11 }}>需求已变更？</Tag>
                </Tooltip>
              )}
              {p.status && p.status !== 'active' && <Tag style={{ margin: 0, fontSize: 11 }}>{p.status}</Tag>}
            </Space>
            {p.quoteText && (
              <div style={{
                fontSize: 12.5, color: '#4e5969', lineHeight: 1.7,
                paddingLeft: 10, borderLeft: '3px solid rgba(78,138,240,0.35)',
              }}>
                {p.quoteText}
                {p.quoteOffset != null && (
                  <span style={{ marginLeft: 8, fontSize: 11, color: '#c9cdd4' }}>
                    原文第 {p.quoteOffset} 字
                  </span>
                )}
              </div>
            )}
          </div>
        ))}
      </div>

      <div>
        <h4 style={{ fontSize: 13, color: '#86909c', marginBottom: 8 }}>
          生成事件 {evs.length > 0 && <span style={{ color: '#c9cdd4' }}>（{evs.length}）</span>}
        </h4>
        {evs.length === 0 ? (
          <span style={{ fontSize: 12, color: '#c9cdd4' }}>
            这条是{data.source === 'ai' ? 'AI 生成的，但没有留下事件记录' : '手工建的，没有生成事件'}
          </span>
        ) : (
          <div style={{ borderLeft: '2px solid rgba(0,0,0,0.05)', paddingLeft: 14, marginLeft: 6 }}>
            {evs.map((e, i) => {
              const cfg = GEN_EVENT_LABEL[e.eventType] || { label: e.eventType, color: 'default' }
              return (
                <div key={i} style={{ paddingBottom: 12, position: 'relative' }}>
                  <span style={{
                    position: 'absolute', left: -19, top: 5, width: 7, height: 7,
                    borderRadius: '50%', background: '#c9cdd4', display: 'block',
                  }} />
                  <Space size={8}>
                    <Tag color={cfg.color} style={{ margin: 0, fontSize: 11 }}>{cfg.label}</Tag>
                    <span style={{ fontSize: 12, color: '#86909c' }}>
                      {(e.createdAt || '').slice(0, 16).replace('T', ' ')}
                    </span>
                    {e.actor && <span style={{ fontSize: 12, color: '#c9cdd4' }}>by {e.actor}</span>}
                  </Space>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {data.generationTaskId && (
        <div style={{ marginTop: 14, fontSize: 11, color: '#c9cdd4' }}>
          生成任务 <span style={{ fontFamily: 'var(--font-mono)' }}>{data.generationTaskId}</span>
        </div>
      )}
    </Card>
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
    ai_reviewed: { label: 'AI 审核', color: '#7c5cbf', icon: '🟡' },
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
