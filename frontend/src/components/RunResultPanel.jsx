import { useState } from 'react'
import { Tag, Button, Space, Tooltip, Spin, message, Tabs } from 'antd'
import {
  CheckCircleOutlined, CloseCircleOutlined, CloseOutlined, LoadingOutlined,
  RightOutlined, DownOutlined, FileTextOutlined, CopyOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

const METHOD_COLORS = { GET: '#0ea5a0', POST: '#0ea5a0', PUT: '#faad14', DELETE: '#e8453c', PATCH: '#7c5cbf' }
const MONO = 'var(--font-mono)'

// 来源徽标配色：一眼区分「环境给的」「上游步骤提取的」「场景变量」
const SRC_COLOR = {
  env: '#0ea5a0', scenario_env: '#0ea5a0', scenario_var: '#7c5cbf',
  extract: '#4e8af0', resource: '#ff7d00', runtime: '#86909c',
  auto_token: '#ff7d00', unknown: '#c9cdd4',
}

// 断言类型 → 人话。**必须和后端 _ASSERT_LABELS 一致**（api_test_runner.py）——
// 两处各写一份的话，同一条断言在报告里和这个抽屉里叫两个名字。
const ASSERT_LABELS = {
  status: '状态码', body_field: '响应字段', body_contains: '响应包含',
  not_contains: '响应不含', header: '响应头', json_path: 'JSONPath', duration: '耗时',
}

/** 一条断言写成人话（通过/没过都用它）：{标签} {字段} {操作符} {期望}，实际 {实际值}
 *
 * 原来只翻译 status 一种，其余一律印成 `断言未通过: body_field` —— 只有内部
 * 类型名，不说是哪个字段、期望什么、实际什么。实测 CC 拿到这句话没法自己修，
 * 只能绕过；而 field/operator/value/actual 这些**本来就在断言对象里带着**，
 * 是被这行代码扔掉的。
 */
function assertText(a, statusCode) {
  const label = ASSERT_LABELS[a.type] || a.type || '断言'
  const field = a.field ? ` ${a.field}` : ''
  const op = a.operator || '=='
  const want = a.value !== undefined && a.value !== null ? a.value : a.expected
  // status 类型的"实际"就是这一步的状态码，断言对象里未必带 actual
  const got = a.actual !== undefined && a.actual !== null
    ? a.actual : (a.type === 'status' ? statusCode : undefined)
  const wantTxt = want === undefined ? '' : ` ${op} ${JSON.stringify(want)}`
  const gotTxt = got === undefined ? '' : `，实际 ${JSON.stringify(got)}`
  return `${label}${field}${wantTxt}${gotTxt}`
}

function fmt(ms) {
  if (!ms && ms !== 0) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

/** 把实际发出的请求还原成可直接执行的 cURL。
    用单引号包裹，内部单引号按 shell 规矩转义成 '\'' —— 不转义的话 JSON 里带引号
    的值会把命令截断，复制出去根本跑不了。 */
function toCurl(req) {
  if (!req?.url) return ''
  const q = (v) => `'${String(v).replace(/'/g, `'\\''`)}'`
  const parts = [`curl -X ${req.method || 'GET'} ${q(req.url)}`]
  for (const [k, v] of Object.entries(req.headers || {})) {
    parts.push(`  -H ${q(`${k}: ${v}`)}`)
  }
  if (req.body != null && req.body !== '') {
    const b = typeof req.body === 'string' ? req.body : JSON.stringify(req.body)
    parts.push(`  -d ${q(b)}`)
  }
  return parts.join(' \\\n')
}

function bodySize(body) {
  if (body == null) return '-'
  const n = new Blob([typeof body === 'string' ? body : JSON.stringify(body)]).size
  return n < 1024 ? `${n}B` : `${(n / 1024).toFixed(1)}KB`
}

function consoleCount(d) {
  return (d.request?.extracted?.length || 0) +
         (d.request?.preScript ? 1 : 0) + (d.request?.postScript ? 1 : 0)
}

function copy(text, label) {
  navigator.clipboard?.writeText(String(text ?? ''))
    .then(() => message.success(`${label || '内容'}已复制`))
    .catch(() => message.warning('复制失败，请手动选中'))
}

function CopyBtn({ text, label }) {
  if (text == null || text === '') return null
  return (
    <Tooltip title={`复制${label || ''}完整值`}>
      <CopyOutlined onClick={(e) => { e.stopPropagation(); copy(text, label) }}
        style={{ fontSize: 11, color: '#86909c', cursor: 'pointer', marginLeft: 6 }} />
    </Tooltip>
  )
}

function SectionTitle({ children, extra }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', fontSize: 11, fontWeight: 600, color: '#86909c', marginBottom: 4, marginTop: 10 }}>
      {children}
      {extra && <span style={{ marginLeft: 'auto', fontWeight: 400 }}>{extra}</span>}
    </div>
  )
}

// 全值展示：不截断、可换行、可复制。定位问题时截断的值等于没有。
function JsonBlock({ data, max = 260 }) {
  if (data == null || data === '') return <span style={{ color: '#c9cdd4', fontSize: 12 }}>无数据</span>
  const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
  return (
    <div style={{ position: 'relative' }}>
      <pre style={{
        fontSize: 11, lineHeight: 1.55, margin: 0, padding: '8px 26px 8px 10px',
        background: 'rgba(0,0,0,0.03)', borderRadius: 6, maxHeight: max,
        overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all', fontFamily: MONO,
      }}>{text}</pre>
      <span style={{ position: 'absolute', top: 6, right: 6 }}><CopyBtn text={text} /></span>
    </div>
  )
}

/** 控制台：只记这一步**自己产出**的东西 —— 提取了什么、脚本干了什么。
    不再打印"使用了哪些变量"：那些值在「实际请求」的 URL 和请求头里已经是解析后的
    真值，再列一遍纯属重复。想知道某个值哪来的，看设置它的那一步的这条日志即可。 */
function ConsoleLines({ extracted, preScript, postScript }) {
  const lines = []
  for (const x of extracted || []) {
    lines.push({
      key: `w-${x.name}`, ok: x.ok, op: '提取',
      text: <>已设置变量 <b>{x.name}</b> = <span style={{ color: x.ok ? '#0e7a76' : '#e8453c' }}>
        {x.ok ? String(x.value) : '取不到（检查 JSONPath 或响应结构）'}</span></>,
      note: `取自响应 ${x.path}`, copy: x.value,
    })
  }
  if (preScript) lines.push({ key: 'pre', ok: true, op: '前置', text: <>执行前置脚本</>, note: String(preScript).slice(0, 160) })
  if (postScript) lines.push({ key: 'post', ok: true, op: '后置', text: <>执行后置脚本</>, note: String(postScript).slice(0, 160) })

  if (!lines.length) {
    return <div style={{ fontSize: 12, color: '#c9cdd4', padding: '8px 2px' }}>本步没有提取变量，也没有前后置脚本</div>
  }
  return (
    <div style={{ fontFamily: MONO, fontSize: 11 }}>
      {lines.map(l => (
        <div key={l.key} style={{
          display: 'flex', gap: 6, padding: '5px 6px', alignItems: 'flex-start',
          borderBottom: '1px solid rgba(0,0,0,0.04)',
          background: l.ok ? 'transparent' : 'rgba(232,69,60,0.05)',
        }}>
          <Tag style={{ margin: 0, fontSize: 11, lineHeight: '16px', padding: '0 5px', flexShrink: 0 }}
            color={l.op === '提取' ? '#4e8af0' : '#86909c'}>{l.op}</Tag>
          <div style={{ flex: 1, wordBreak: 'break-all' }}>
            <div>{l.text}</div>
            <div style={{ color: '#c9cdd4', marginTop: 1 }}>{l.note}</div>
          </div>
          <CopyBtn text={l.copy} label={l.key} />
        </div>
      ))}
    </div>
  )
}

/** 断言结果：期望 + 实际，一行一条 */
function Assertions({ items, statusCode }) {
  if (!items?.length) return <div style={{ fontSize: 12, color: '#c9cdd4' }}>本步没有断言</div>
  // 和收起行用**同一个**渲染器：两处各写一份的话，同一条断言在两个地方
  // 说法不一样；而且这里原本只认 status/body_contains/body_field 三种，
  // 其余（header / json_path / duration / not_contains）直接把整个对象
  // JSON.stringify 甩给用户看。
  const desc = (a) => assertText(a, statusCode)
  return (
    <div>
      {items.map((a, j) => (
        <div key={j} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 11, padding: '3px 0' }}>
          <span style={{ color: '#86909c', minWidth: 14 }}>{j + 1}.</span>
          {a.passed ? <CheckCircleOutlined style={{ color: '#0ea5a0', fontSize: 12, marginTop: 2 }} />
                    : <CloseCircleOutlined style={{ color: '#e8453c', fontSize: 12, marginTop: 2 }} />}
          <span style={{ fontFamily: MONO, wordBreak: 'break-all' }}>
            {desc(a)}
            {/* 断言本身写错时要说是写错了，别混在"没通过"里让人去查被测系统 */}
            {a.error && <div style={{ color: '#ff7d00', marginTop: 2 }}>{a.error}</div>}
          </span>
        </div>
      ))}
    </div>
  )
}

/** 名称 / 值 两列表 —— 请求头、查询参数都用它，字段名和值分开对齐，不再挤成一行文本 */
function KVTable({ data }) {
  const rows = Array.isArray(data)
    ? data.filter(r => r && (r.key ?? r.name)).map(r => [r.key ?? r.name, r.value])
    : Object.entries(data || {})
  if (!rows.length) return null
  return (
    <div style={{ border: '1px solid rgba(0,0,0,0.06)', borderRadius: 6, overflow: 'hidden' }}>
      <div style={{ display: 'flex', background: 'rgba(0,0,0,0.03)', fontSize: 11, color: '#86909c', fontWeight: 600 }}>
        <div style={{ width: 132, flexShrink: 0, padding: '4px 8px' }}>名称</div>
        <div style={{ flex: 1, padding: '4px 8px' }}>值</div>
      </div>
      {rows.map(([k, v], i) => (
        <div key={`${k}-${i}`} style={{
          display: 'flex', fontSize: 11, fontFamily: MONO, alignItems: 'flex-start',
          borderTop: '1px solid rgba(0,0,0,0.05)',
        }}>
          <div style={{ width: 132, flexShrink: 0, padding: '5px 8px', color: '#4e5969', wordBreak: 'break-all' }}>{k}</div>
          <div style={{ flex: 1, padding: '5px 8px', wordBreak: 'break-all', display: 'flex', gap: 4 }}>
            <span style={{ flex: 1 }}>{String(v ?? '')}</span>
            <CopyBtn text={v} label={k} />
          </div>
        </div>
      ))}
    </div>
  )
}

/** 实际请求：真正发出去的那一份，按 URL / 查询参数 / 请求头 / 请求体 / cURL 分区列清楚 */
function ActualRequest({ req }) {
  if (!req) return <div style={{ fontSize: 12, color: '#c9cdd4' }}>无请求数据</div>
  const headers = req.headers || {}
  const ctype = Object.entries(headers).find(([k]) => k.toLowerCase() === 'content-type')?.[1]
  const paramRows = Array.isArray(req.params)
    ? req.params.filter(p => p && (p.key ?? p.name))
    : Object.entries(req.params || {}).map(([key, value]) => ({ key, value }))

  return (
    <div style={{ fontSize: 11 }}>
      {/* cURL 不铺出来占地方 —— 内容跟下面的 URL/头/体完全重复，点一下拿走就行 */}
      <SectionTitle extra={
        <a onClick={() => copy(toCurl(req), 'cURL')} style={{ fontSize: 11 }}>复制 cURL</a>
      }>请求 URL</SectionTitle>
      <div style={{
        fontFamily: MONO, wordBreak: 'break-all', padding: '6px 8px',
        background: 'rgba(0,0,0,0.03)', borderRadius: 6,
      }}>
        <Tag color={METHOD_COLORS[req.method]} style={{ fontSize: 11, lineHeight: '16px', padding: '0 5px' }}>{req.method}</Tag>
        {req.url}<CopyBtn text={req.url} label="URL" />
      </div>

      {paramRows.length > 0 && (<>
        <SectionTitle>查询参数 {paramRows.length}</SectionTitle>
        <KVTable data={paramRows} />
      </>)}

      {Object.keys(headers).length > 0 && (<>
        <SectionTitle>请求头 {Object.keys(headers).length}</SectionTitle>
        <KVTable data={headers} />
      </>)}

      {req.body != null && req.body !== '' && (<>
        <SectionTitle>请求体 {ctype && <Tag style={{ marginLeft: 6, fontSize: 11, lineHeight: '16px', padding: '0 5px' }}>{String(ctype).split(';')[0]}</Tag>}</SectionTitle>
        <JsonBlock data={req.body} max={220} />
      </>)}
    </div>
  )
}

export default function RunResultPanel({ results, scenario, running, onClose, reportId, envName, projectId }) {
  const [expandedId, setExpandedId] = useState(null)
  const navigate = useNavigate()

  const passCount = results.filter(r => r.status === 'pass').length
  const failCount = results.filter(r => r.status === 'fail').length
  const skipCount = results.filter(r => r.status === 'skip').length
  const totalDuration = results.reduce((s, r) => s + (r.duration || 0), 0)

  // 整体结论：跑完只要有一步失败就是失败，别让人对着几个数字自己算
  const verdict = running
    ? { label: '执行中', color: '#0ea5a0', icon: <LoadingOutlined /> }
    : failCount > 0
      ? { label: '失败', color: '#e8453c', icon: <CloseCircleOutlined /> }
      : { label: '通过', color: '#0ea5a0', icon: <CheckCircleOutlined /> }

  // 详情优先取本次运行事件自带的（后端 step_result 直接带 request/response/断言/error）。
  // scenario.steps[].lastResponse 是打开页面时加载的那一份，跑完不刷新就是旧的甚至没有，
  // 只靠它会让刚跑完的步骤展开显示「暂无详情数据」。
  const getStepDetail = (r) => {
    if (r && (r.request || r.error || r.responseBody !== undefined || r.assertions)) {
      return {
        request: r.request, error: r.error, body: r.responseBody,
        assertions: r.assertions, statusCode: r.statusCode, duration: r.duration,
      }
    }
    const step = (scenario?.steps || []).find(s => s.id === r?.stepId)
    return step?.lastResponse || null
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* 顶部统计 */}
      <div style={{ padding: '10px 16px', borderBottom: '1px solid rgba(0,0,0,0.06)', flexShrink: 0 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Space size={8}>
            <span style={{ fontWeight: 600, fontSize: 14 }}>运行结果</span>
            {running && <Spin size="small" indicator={<LoadingOutlined />} />}
          </Space>
          <Button type="text" size="small" icon={<CloseOutlined />} onClick={onClose} />
        </div>

        {/* 整体结论摆在最前面。以前只有「5 通过 / 0 失败 / 共 5 步」，
            到底算过没过要人自己心算一下，一眼看不出来。 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 8 }}>
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: 6,
            padding: '3px 12px', borderRadius: 999, fontSize: 13, fontWeight: 700,
            color: '#fff', background: verdict.color,
          }}>
            {verdict.icon} {verdict.label}
          </span>
          <span style={{ fontSize: 12, color: '#4e5969' }}>
            {passCount}/{results.length} 步通过
            {failCount > 0 && <span style={{ color: '#e8453c', fontWeight: 600 }}>，{failCount} 步失败</span>}
            {skipCount > 0 && <span style={{ color: '#c9cdd4' }}>，{skipCount} 跳过</span>}
          </span>
          <span style={{ fontSize: 12, color: '#86909c', marginLeft: 'auto' }}>{fmt(totalDuration)}</span>
        </div>
        {envName && <div style={{ fontSize: 11, color: '#c9cdd4', marginTop: 4 }}>环境: {envName}</div>}
      </div>

      {/* 步骤列表 */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {results.map((r, i) => {
          const isExpanded = expandedId === r.stepId
          const detail = isExpanded ? getStepDetail(r) : null
          const isFail = r.status === 'fail'
          // 失败原因直接摆在行上。请求都没发出去的那种失败（变量未解析、连不上）没有
          // 状态码也没有耗时，不写出来这一行就完全不说话。
          const failHint = isFail
            ? (r.error || (r.assertions || []).filter(a => !a.passed)
                .map(a => assertText(a, r.statusCode))
                .join('；'))
            : null

          return (
            <div key={r.stepId || i}>
              {/* 步骤行 */}
              <div
                onClick={() => setExpandedId(isExpanded ? null : r.stepId)}
                style={{
                  padding: '8px 16px', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 8,
                  background: isFail ? 'rgba(232,69,60,0.04)' : 'transparent',
                  borderLeft: isFail ? '3px solid #e8453c' : '3px solid transparent',
                  borderBottom: '1px solid rgba(0,0,0,0.03)',
                }}
                onMouseEnter={e => { if (!isFail) e.currentTarget.style.background = 'rgba(0,0,0,0.02)' }}
                onMouseLeave={e => { if (!isFail) e.currentTarget.style.background = 'transparent' }}
              >
                {r.status === 'pass' ? <CheckCircleOutlined style={{ color: '#0ea5a0', fontSize: 14 }} /> :
                 r.status === 'fail' ? <CloseCircleOutlined style={{ color: '#e8453c', fontSize: 14 }} /> :
                 r.status === 'skip' ? <span style={{ width: 14, height: 14, borderRadius: 7, background: 'rgba(0,0,0,0.08)', display: 'inline-block' }} /> :
                 <LoadingOutlined style={{ color: '#0ea5a0', fontSize: 14 }} />}

                {r.statusCode && (
                  <Tag color={r.statusCode < 400 ? '#0ea5a0' : '#e8453c'}
                    style={{ fontSize: 11, margin: 0, padding: '0 4px', lineHeight: '18px', minWidth: 32, textAlign: 'center' }}>
                    {r.statusCode}
                  </Tag>
                )}

                <Tag color={METHOD_COLORS[r.method]} style={{ fontSize: 11, margin: 0, padding: '0 4px', lineHeight: '18px' }}>
                  {r.method || 'GET'}
                </Tag>

                <span style={{ flex: 1, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {r.stepName}
                </span>

                <span style={{ fontSize: 11, color: '#c9cdd4', flexShrink: 0 }}>{fmt(r.duration)}</span>

                {isExpanded ? <DownOutlined style={{ fontSize: 11, color: '#c9cdd4' }} /> :
                              <RightOutlined style={{ fontSize: 11, color: '#c9cdd4' }} />}
              </div>

              {failHint && !isExpanded && (
                <div style={{
                  padding: '4px 16px 6px 44px', fontSize: 11, color: '#e8453c',
                  background: 'rgba(232,69,60,0.04)', borderLeft: '3px solid #e8453c',
                  borderBottom: '1px solid rgba(0,0,0,0.03)',
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                }} title={failHint}>{failHint}</div>
              )}

              {/* 展开详情 */}
              {isExpanded && detail && (
                <div style={{ padding: '8px 14px 12px 26px', background: 'rgba(0,0,0,0.02)', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                  {/* 一行状态条：状态码 / 耗时 / 大小 */}
                  <div style={{ display: 'flex', gap: 14, fontSize: 11, color: '#4e5969', padding: '5px 8px', background: 'rgba(0,0,0,0.03)', borderRadius: 6, marginBottom: 8 }}>
                    <span>HTTP 状态码：<b style={{ color: (detail.statusCode ?? 0) < 400 && detail.statusCode ? '#0ea5a0' : '#e8453c' }}>{detail.statusCode ?? '未发出'}</b></span>
                    <span>耗时：<b>{fmt(detail.duration)}</b></span>
                    <span>大小：<b>{bodySize(detail.body)}</b></span>
                  </div>

                  {detail.error && (
                    <div style={{ padding: '6px 10px', background: 'var(--red-bg)', border: '1px solid #ffccc7', borderRadius: 6, fontSize: 11, color: '#e8453c', whiteSpace: 'pre-wrap', marginBottom: 8 }}>
                      {detail.error}
                    </div>
                  )}

                  {/* 断言结果常驻在页签之上——跑挂了第一眼要看的就是它 */}
                  {detail.assertions?.length > 0 && (
                    <div style={{ marginBottom: 8 }}>
                      <SectionTitle extra={`${detail.assertions.filter(a => a.passed).length}/${detail.assertions.length} 通过`}>断言结果</SectionTitle>
                      <Assertions items={detail.assertions} statusCode={detail.statusCode} />
                    </div>
                  )}

                  {/* 其余内容收进页签，不再一路向下铺开 */}
                  <Tabs
                    size="small"
                    defaultActiveKey={detail.error ? 'req' : 'body'}
                    items={[
                      { key: 'body', label: '响应体', children: <JsonBlock data={detail.body} max={300} /> },
                      {
                        key: 'console',
                        label: `控制台${consoleCount(detail) ? ` ${consoleCount(detail)}` : ''}`,
                        children: <ConsoleLines extracted={detail.request?.extracted} preScript={detail.request?.preScript} postScript={detail.request?.postScript} />,
                      },
                      { key: 'req', label: '实际请求', children: <ActualRequest req={detail.request} /> },
                    ]}
                  />
                </div>
              )}

              {isExpanded && !detail && (
                <div style={{ padding: '12px 28px', color: '#c9cdd4', fontSize: 12 }}>
                  {running ? '步骤执行中，结束后显示详情...' : '暂无详情数据'}
                </div>
              )}
            </div>
          )
        })}

        {running && results.length > 0 && (
          <div style={{ padding: '12px 16px', textAlign: 'center' }}>
            <Spin size="small" /> <span style={{ marginLeft: 8, fontSize: 12, color: '#86909c' }}>执行中...</span>
          </div>
        )}
      </div>

      {/* 底部：报告链接 */}
      {reportId && !running && (
        <div style={{ padding: '8px 16px', borderTop: '1px solid rgba(0,0,0,0.06)', flexShrink: 0 }}>
          <Button type="link" icon={<FileTextOutlined />} size="small"
            onClick={() => navigate(`/projects/${projectId}/reports/${reportId}`)}>
            查看完整测试报告
          </Button>
        </div>
      )}
    </div>
  )
}
