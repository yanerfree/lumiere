import { useState, useMemo, useEffect, useCallback } from 'react'
import { Card, Tag, Button, Radio, Space, Spin, Empty, Input, Tooltip, Drawer, Tabs, message } from 'antd'
import {
  DownloadOutlined, ArrowLeftOutlined, SyncOutlined, RightOutlined,
  SearchOutlined, CheckCircleFilled, CloseCircleFilled, ExclamationCircleFilled,
  ClockCircleOutlined, MinusCircleFilled, LoadingOutlined,
} from '@ant-design/icons'
import { useNavigate, useParams } from 'react-router-dom'
import { api } from '../../utils/request'
import FailureTriagePanel from '../../components/FailureTriagePanel'

// 平台按规则算出来的失败现象 —— 只说"是什么"，不说"为什么"。
// 摊在收起的行上，QA 扫一眼就能把 20 条失败分堆，不用一条条展开。
const phenomenonLabel = {
  timeout: '超时',
  element_not_found: '元素找不到',
  assertion_mismatch: '断言不符',
  http_5xx: '服务端 5xx',
  script_error: '脚本自身报错',
  dependency_unresolved: '依赖没解析',
  unknown: '待判定',
}
const causeLabel = {
  product_defect: '系统缺陷',
  test_defect: '脚本写错',
  case_expired: '用例过期',
  env_issue: '环境问题',
  data_issue: '数据问题',
  flaky: '不稳定',
  unknown: '看不出来',
}

const statusCfg = {
  passed: { label: '通过', color: '#0ea5a0', dot: '#0ea5a0' },
  failed: { label: '失败', color: '#e8453c', dot: '#e8453c' },
  error: { label: '错误', color: '#faad14', dot: '#faad14' },
  skipped: { label: '跳过', color: '#c9cdd4', dot: '#c9cdd4' },
  // 重试之后才通过的 —— 一次就过和试了三次才过不是一回事，所以单独一档，
  // 而且它进通过率分母（见 execution_service）
  flaky: { label: '重试后通过', color: '#ff7d00', dot: '#ff7d00' },
  // pytest 的预期失败：跑了、按预期失败。以前被并进"跳过"，读起来像没跑
  xfail: { label: '预期失败', color: '#7c5cbf', dot: '#7c5cbf' },
  running: { label: '执行中', color: '#0ea5a0', dot: '#0ea5a0' },
  pending: { label: '待执行', color: '#c9cdd4', dot: '#c9cdd4' },
}

const methodColor = { GET: '#0ea5a0', POST: '#0ea5a0', PUT: '#faad14', DELETE: '#e8453c', PATCH: '#7c5cbf' }
const phaseColor = { setup: '#7c5cbf', action: '#0ea5a0', verify: '#0ea5a0' }
const phaseLabel = { setup: '准备', action: '操作', verify: '验证' }

function fmt(ms) {
  if (!ms && ms !== 0) return '-'
  if (ms < 1000) return ms + 'ms'
  if (ms < 60000) return (ms / 1000).toFixed(2) + 's'
  return Math.floor(ms / 60000) + 'm ' + Math.round((ms % 60000) / 1000) + 's'
}

function PassRateRing({ rate, passed, total, size = 160, running = false, done = 0 }) {
  const r = size / 2 - 10
  const c = 2 * Math.PI * r
  // 通过率用外面按规范口径算好的 rate（skipped / xfail 不进分母），这里不再自己
  // 拿 passed/total 算一遍 —— 那个分母含跳过，于是"一条没跑、跳过 2 条"的报告会
  // 显示成**红色的 0%**，看着像全挂了。实测：沙箱建不起来导致整批没开跑，
  // 库里 pass_rate 是 NULL，页面却是个鲜红的 0%。
  const hasRate = !running && rate != null
  const pct = running ? (total > 0 ? (done / total) * 100 : 0) : (hasRate ? Number(rate) : 0)
  const offset = c - (c * pct) / 100
  const color = running ? '#0ea5a0'
    : !hasRate ? '#c9cdd4'
    : pct >= 95 ? '#0ea5a0' : pct >= 80 ? '#faad14' : '#e8453c'
  return (
    <svg width={size} height={size} style={{ display: 'block', flexShrink: 0 }}>
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke="rgba(0,0,0,0.04)" strokeWidth={10} />
      <circle cx={size/2} cy={size/2} r={r} fill="none" stroke={color} strokeWidth={10}
        strokeDasharray={c} strokeDashoffset={offset} strokeLinecap="round"
        transform={`rotate(-90 ${size/2} ${size/2})`}
        style={{ transition: 'stroke-dashoffset 0.6s ease' }} />
      {/* 跑完了就显示通过率（这个环本来就叫 PassRateRing）。
          原来标签写「已完成」、数字却是**通过数** —— 一份跑完 1 条、失败 1 条的报告
          上会显示「已完成 0」，和旁边的「执行: 1」直接打架。而且通过数右边已经
          单独列了一份，环里重复一遍没有信息增量。 */}
      <text x={size/2} y={size/2 - 14} textAnchor="middle" dominantBaseline="central"
        style={{ fontSize: 13, fill: running ? '#0ea5a0' : '#86909c' }}>
        {running ? '执行中' : hasRate ? '通过率' : '未执行'}</text>
      <text x={size/2} y={size/2 + 10} textAnchor="middle" dominantBaseline="central"
        style={{ fontSize: running ? 22 : 28, fontWeight: 700, fill: hasRate || running ? '#1d2129' : '#c9cdd4' }}>
        {running ? `${done}/${total}` : hasRate ? `${pct.toFixed(pct % 1 === 0 ? 0 : 1)}%` : '—'}
      </text>
    </svg>
  )
}

function StatusIcon({ status, size = 16 }) {
  const s = { fontSize: size, lineHeight: 1 }
  switch (status) {
    case 'passed': return <CheckCircleFilled style={{ ...s, color: '#0ea5a0' }} />
    case 'failed': return <CloseCircleFilled style={{ ...s, color: '#e8453c' }} />
    case 'error': return <ExclamationCircleFilled style={{ ...s, color: '#faad14' }} />
    case 'skipped': return <MinusCircleFilled style={{ ...s, color: '#c9cdd4' }} />
    case 'running': return <LoadingOutlined style={{ ...s, color: '#0ea5a0' }} spin />
    default: return <ClockCircleOutlined style={{ ...s, color: '#c9cdd4' }} />
  }
}

function StatusDot({ status }) {
  return <StatusIcon status={status} size={14} />
}

function JsonBlock({ data, maxHeight = 500 }) {
  const raw = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
  const highlight = (line) => {
    const parts = []
    let rest = line
    const keyMatch = rest.match(/^(\s*)"([^"]+)"(\s*:\s*)/)
    if (keyMatch) {
      parts.push(<span key="i">{keyMatch[1]}</span>)
      parts.push(<span key="k" style={{ color: '#953800' }}>"{keyMatch[2]}"</span>)
      parts.push(<span key="c" style={{ color: '#383a42' }}>{keyMatch[3]}</span>)
      rest = rest.slice(keyMatch[0].length)
    }
    const strMatch = rest.match(/^"([^"]*)"(.*)/)
    if (strMatch) {
      parts.push(<span key="s" style={{ color: '#50a14f' }}>"{strMatch[1]}"</span>)
      if (strMatch[2]) parts.push(<span key="a" style={{ color: '#383a42' }}>{strMatch[2]}</span>)
      return parts
    }
    const numMatch = rest.match(/^(-?\d+\.?\d*)(,?\s*)$/)
    if (numMatch) {
      parts.push(<span key="n" style={{ color: '#986801' }}>{numMatch[1]}</span>)
      if (numMatch[2]) parts.push(<span key="a2" style={{ color: '#383a42' }}>{numMatch[2]}</span>)
      return parts
    }
    const boolMatch = rest.match(/^(true|false|null)(,?\s*)$/)
    if (boolMatch) {
      parts.push(<span key="b" style={{ color: '#0184bc' }}>{boolMatch[1]}</span>)
      if (boolMatch[2]) parts.push(<span key="a3" style={{ color: '#383a42' }}>{boolMatch[2]}</span>)
      return parts
    }
    parts.push(<span key="r" style={{ color: '#383a42' }}>{rest}</span>)
    return parts
  }
  const lines = raw.split('\n')
  return (
    <div style={{ background: 'transparent', borderRadius: 12, overflow: 'hidden' }}>
      <div style={{ overflow: 'auto', maxHeight, padding: '10px 0',
        fontFamily: 'var(--font-mono)', fontSize: 12, lineHeight: 1.9, color: '#383a42',
      }}>
        {lines.map((line, i) => (
          <div key={i} style={{ display: 'flex', minHeight: 22, paddingRight: 14 }}>
            <span style={{ width: 38, textAlign: 'right', paddingRight: 14, color: '#c9cdd4', fontSize: 11, flexShrink: 0, userSelect: 'none', borderRight: '1px solid rgba(0,0,0,0.04)' }}>{i + 1}</span>
            <span style={{ flex: 1, whiteSpace: 'pre', paddingLeft: 14 }}>{highlight(line)}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

// 报告里一律显示**实际发出**的地址。step.url 存的可能是步骤定义里的模板
// （${BASE_URL}/api/...），而同一屏的请求头却是真 token —— 一半变量一半真值，
// 拿它没法定位问题。后端新报告已存真实地址，这里兼容历史报告。
const stepRealUrl = (step) => step?.requestData?.url || step?.url

function HeadersTable({ headers }) {
  if (!headers || typeof headers !== 'object') return null
  const entries = Object.entries(headers)
  if (entries.length === 0) return null
  return (
    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
      <thead>
        <tr style={{ borderBottom: '1px solid rgba(0,0,0,0.05)' }}>
          <th style={{ textAlign: 'left', padding: '8px 0', fontWeight: 500, color: '#1d2129', width: 200 }}>名称</th>
          <th style={{ textAlign: 'left', padding: '8px 0', fontWeight: 500, color: '#1d2129' }}>值</th>
        </tr>
      </thead>
      <tbody>
        {entries.map(([k, v]) => (
          <tr key={k} style={{ borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
            <td style={{ padding: '8px 0', color: '#4e5969', fontFamily: 'var(--font-mono)', fontSize: 12, verticalAlign: 'top' }}>{k}</td>
            <td style={{ padding: '8px 0', color: '#86909c', fontFamily: 'var(--font-mono)', fontSize: 12, wordBreak: 'break-all' }}>{v}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function StepDetailDrawer({ step, open, onClose }) {
  if (!step) return null
  const isFailed = step.status === 'failed' || step.status === 'error'
  const mc = methodColor[step.httpMethod] || '#86909c'

  const reqBody = step.requestData?.body ?? (step.requestData && !step.requestData.headers ? step.requestData : null)
  const respBody = step.responseData?.body ?? (step.responseData && !step.responseData.headers ? step.responseData : null)
  const reqHeaders = step.requestData?.headers
  const respHeaders = step.responseData?.headers
  // 优先显示**实际发出**的地址。step.url 可能是步骤定义里的模板（${BASE_URL}/...），
  // 而旁边的请求头却是真 token —— 一半变量一半真值，没法拿来定位问题。
  // 后端新报告已经存真实地址了，这里兜住历史报告。
  const realUrl = stepRealUrl(step)

  const tabItems = []
  if (reqBody != null) tabItems.push({ key: 'body', label: '请求体', children: <JsonBlock data={reqBody} /> })
  if (reqHeaders && Object.keys(reqHeaders).length > 0) tabItems.push({ key: 'header', label: `请求头 (${Object.keys(reqHeaders).length})`, children: <HeadersTable headers={reqHeaders} /> })
  if (respBody != null) tabItems.push({ key: 'resp-body', label: '响应体', children: <JsonBlock data={respBody} /> })
  if (respHeaders && Object.keys(respHeaders).length > 0) tabItems.push({ key: 'resp-header', label: `响应头 (${Object.keys(respHeaders).length})`, children: <HeadersTable headers={respHeaders} /> })

  return (
    <Drawer
      title={null}
      open={open}
      onClose={onClose}
      width={680}
      styles={{ header: { display: 'none' }, body: { padding: 0 } }}
    >
      {/* Step name */}
      <div style={{ padding: '16px 24px 0', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          {step.stepPhase && (
            <span style={{
              fontSize: 11, fontWeight: 600, padding: '1px 8px', borderRadius: 8,
              background: `${phaseColor[step.stepPhase] || '#86909c'}15`,
              color: phaseColor[step.stepPhase] || '#86909c',
            }}>{phaseLabel[step.stepPhase] || step.stepPhase}</span>
          )}
          <span style={{ fontSize: 15, fontWeight: 600, color: '#1d2129' }}>
            {step.stepLabel || step.stepName}
          </span>
        </div>

        {/* Status line */}
        <div style={{ fontSize: 13, color: '#4e5969', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
          {step.statusCode && (
            <>
              <span style={{ color: '#86909c' }}>HTTP 状态码:</span>
              <span style={{ color: step.statusCode >= 400 ? '#e8453c' : '#0ea5a0', fontWeight: 600 }}>{step.statusCode}</span>
              <span style={{ color: 'rgba(0,0,0,0.15)', margin: '0 4px' }}>|</span>
            </>
          )}
          <span style={{ color: '#86909c' }}>耗时:</span>
          <span style={{ fontWeight: 500 }}>{fmt(step.durationMs)}</span>
        </div>

        {/* Error banner */}
        {isFailed && step.errorSummary && (
          <div style={{
            display: 'flex', alignItems: 'flex-start', gap: 8, padding: '10px 14px', marginBottom: 14,
            background: 'var(--red-bg)', borderRadius: 12, border: '1px solid #ffccc7',
          }}>
            <CloseCircleFilled style={{ color: '#e8453c', fontSize: 14, marginTop: 2, flexShrink: 0 }} />
            <span style={{ fontSize: 12, color: '#4e5969', lineHeight: 1.6 }}>{step.errorSummary}</span>
          </div>
        )}

        {/* Assertions */}
        {step.assertions?.length > 0 && (
          <div style={{ marginBottom: 14 }}>
            <div style={{ fontSize: 13, fontWeight: 600, color: '#1d2129', marginBottom: 8 }}>断言结果</div>
            {step.assertions.map((a, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, lineHeight: 2 }}>
                <span style={{ color: '#86909c', minWidth: 16 }}>{i + 1}.</span>
                {a.passed
                  ? <CheckCircleFilled style={{ color: '#0ea5a0', fontSize: 13 }} />
                  : <CloseCircleFilled style={{ color: '#e8453c', fontSize: 13 }} />}
                <span style={{ color: '#4e5969' }}>{a.description || a.message || JSON.stringify(a)}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Request URL (only when HTTP data exists) */}
      {step.httpMethod && realUrl && (
        <div style={{ padding: '16px 24px', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
          <div style={{ fontSize: 13, fontWeight: 600, color: '#1d2129', marginBottom: 8 }}>请求 URL:</div>
          <div style={{ fontSize: 13, fontFamily: 'var(--font-mono)', lineHeight: 1.6, wordBreak: 'break-all' }}>
            <span style={{ color: mc, fontWeight: 700 }}>{step.httpMethod}</span>
            {'  '}
            <span style={{ color: '#4e5969' }}>{realUrl}</span>
          </div>
          {realUrl !== step.url && (
            <div style={{ fontSize: 11, color: '#c9cdd4', marginTop: 4, fontFamily: 'var(--font-mono)' }}>
              模板：{step.url}
            </div>
          )}
        </div>
      )}

      {/* Tabs: Body / Header / Response */}
      {tabItems.length > 0 && (
        <div style={{ padding: '0 24px 24px' }}>
          <Tabs
            size="small"
            defaultActiveKey={isFailed && respBody ? 'resp-body' : tabItems[0]?.key}
            items={tabItems}
          />
        </div>
      )}
    </Drawer>
  )
}

function parseExecutionLog(log) {
  if (!log) return { testName: null, result: null, duration: null, errorLines: [], outputLines: [] }
  const lines = log.split('\n')
  let testName = null, result = null, duration = null
  const errorLines = []
  const outputLines = []
  let inError = false

  for (const line of lines) {
    const trimmed = line.trim()
    if (!trimmed) continue

    // 提取测试结果行: tests/e2e/test_smoke.py::TestClass::test_func PASSED
    const resultMatch = trimmed.match(/^(tests\/\S+::\S+)\s+(PASSED|FAILED|ERROR)/i)
    if (resultMatch) {
      testName = resultMatch[1]
      result = resultMatch[2]
      continue
    }

    // 提取耗时: 1 passed in 0.71s / 1 failed in 2.3s
    const durMatch = trimmed.match(/(\d+)\s+(?:passed|failed|error).*?in\s+([\d.]+s)/i)
    if (durMatch) { duration = durMatch[2]; continue }

    // 跳过 pytest header/footer 噪音
    if (trimmed.startsWith('===') || trimmed.startsWith('---') || trimmed.startsWith('platform ')
      || trimmed.startsWith('cachedir:') || trimmed.startsWith('rootdir:')
      || trimmed.startsWith('configfile:') || trimmed.startsWith('plugins:')
      || trimmed.startsWith('asyncio:') || trimmed.startsWith('collecting')
      || trimmed.startsWith('collected') || trimmed.startsWith('generated xml')) continue

    // 错误/断言行
    if (trimmed.startsWith('E ') || trimmed.startsWith('> ') || trimmed.includes('AssertionError')
      || trimmed.includes('assert ') || trimmed.startsWith('FAILED')) {
      inError = true
      errorLines.push(line)
      continue
    }
    if (inError && (trimmed.startsWith('  ') || trimmed.startsWith('File '))) {
      errorLines.push(line)
      continue
    }
    inError = false

    // 其余有意义的输出
    if (trimmed.length > 2) outputLines.push(line)
  }

  return { testName, result, duration, errorLines, outputLines }
}

function ScenarioExpanded({ scenario, projectId, onConfirmed }) {
  const { caseSteps, preconditions, expectedResult, errorSummary, executionLog, status, scriptRefFile, scriptRefFunc, durationMs, remark, startedAt, completedAt, runId, branchId, caseId } = scenario
  const isFailed = status === 'failed' || status === 'error'
  const isPassed = status === 'passed'
  const parsed = parseExecutionLog(executionLog)
  const hasRetry = remark && remark.includes('重试')

  return (
    <div style={{ padding: '16px 20px 16px 48px', display: 'flex', flexDirection: 'column', gap: 14 }}>
      {/* 执行信息卡片 */}
      <div style={{ padding: '12px 16px', background: isPassed ? '#e0f7f6' : isFailed ? '#fff2f0' : 'rgba(0,0,0,0.02)', borderRadius: 12, border: `1px solid ${isPassed ? 'rgba(14,165,160,0.2)' : isFailed ? 'rgba(232,69,60,0.15)' : 'rgba(0,0,0,0.04)'}` }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            <StatusIcon status={status} size={18} />
            <span style={{ fontWeight: 600, fontSize: 14, color: isPassed ? '#0ea5a0' : isFailed ? '#e8453c' : '#86909c' }}>
              {isPassed ? '执行通过' : isFailed ? '执行失败' : status === 'skipped' ? '已跳过' : status === 'running' ? '执行中' : '待执行'}
            </span>
            {hasRetry && (
              <Tag style={{ color: '#faad14', border: 'none', background: 'transparent', fontSize: 11 }}>{remark}</Tag>
            )}
          </div>
          <span style={{ fontSize: 13, color: '#86909c', fontFamily: 'var(--font-mono)' }}>
            {durationMs ? fmt(durationMs) : parsed.duration || '-'}
          </span>
        </div>
        {(scriptRefFile || parsed.testName) && (
          <div style={{ fontSize: 12, color: '#86909c', fontFamily: 'var(--font-mono)' }}>
            {parsed.testName || `${scriptRefFile}${scriptRefFunc ? `::${scriptRefFunc}` : ''}`}
          </div>
        )}
        {startedAt && (
          <div style={{ fontSize: 12, color: '#86909c', marginTop: 4 }}>
            开始: {new Date(startedAt).toLocaleString('zh-CN')}
            {completedAt && <span style={{ marginLeft: 16 }}>结束: {new Date(completedAt).toLocaleString('zh-CN')}</span>}
          </div>
        )}
      </div>

      {/* 失败原因。
          skipped 也要走这儿：一条用例"为什么没跑"和"为什么挂了"同样需要交代，
          而列表里那句被截成 200px 宽的省略号，用户根本读不到后半句
          （沙箱失败那条恰恰把"去哪儿改"写在后半句）。 */}
      {(isFailed || status === 'skipped') && (errorSummary || parsed.errorLines.length > 0) && (
        <div>
          <div style={{ fontSize: 12, color: isFailed ? '#e8453c' : '#86909c', marginBottom: 6, fontWeight: 600 }}>
            {isFailed ? '失败原因' : '未执行的原因'}</div>
          {errorSummary && (
            <div style={{ fontSize: 13, color: isFailed ? '#e8453c' : '#4e5969', padding: '10px 14px', background: isFailed ? '#fff2f0' : 'rgba(0,0,0,0.02)', borderRadius: 12, border: `1px solid ${isFailed ? 'rgba(232,69,60,0.15)' : 'rgba(0,0,0,0.06)'}`, marginBottom: parsed.errorLines.length > 0 ? 8 : 0, lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>
              {errorSummary}
            </div>
          )}
          {parsed.errorLines.length > 0 && (
            <pre style={{
              margin: 0, padding: '10px 14px', background: 'rgba(0,0,0,0.02)', color: '#e8453c',
              borderRadius: 12, fontSize: 12, lineHeight: 1.5, overflow: 'auto', maxHeight: 200,
              whiteSpace: 'pre-wrap', wordBreak: 'break-all', border: '1px solid rgba(0,0,0,0.04)',
              fontFamily: 'var(--font-mono)',
            }}>{parsed.errorLines.join('\n')}</pre>
          )}
        </div>
      )}

      {/* 三层失败判断：平台现象 / CC 归因 / 人工确认。
          QA 看失败就在这一页，原来这里只有一句指向别处的提示，等于把人踢走。 */}
      {isFailed && runId && branchId && caseId && (
        <FailureTriagePanel
          projectId={projectId} branchId={branchId} caseId={caseId}
          run={{ id: runId }} onConfirmed={onConfirmed}
        />
      )}
      {isFailed && !runId && (
        <div style={{ padding: '8px 12px', background: 'rgba(78,138,240,0.06)', borderRadius: 12, border: '1px solid rgba(78,138,240,0.2)' }}>
          <span style={{ fontSize: 12, color: '#4e8af0' }}>
            这条没有脚本执行记录（手工录入或接口测试场景），失败归因看上面的错误信息和步骤明细。
          </span>
        </div>
      )}

      {/* 用例步骤（如果有定义） */}
      {caseSteps && caseSteps.length > 0 && (
        <div>
          <div style={{ fontSize: 12, color: '#86909c', marginBottom: 6, fontWeight: 600 }}>测试步骤</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
            {caseSteps.map((step, i) => (
              <div key={i} style={{
                display: 'flex', alignItems: 'flex-start', gap: 10, padding: '7px 12px',
                background: 'transparent', borderRadius: 12, border: '1px solid rgba(0,0,0,0.04)',
              }}>
                <span style={{
                  width: 20, height: 20, borderRadius: '50%', flexShrink: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 11, fontWeight: 600, color: '#fff',
                  background: isPassed ? '#0ea5a0' : '#c9cdd4',
                }}>{step.seq || i + 1}</span>
                {step.phase && (
                  <span style={{
                    fontSize: 11, fontWeight: 600, padding: '1px 6px', borderRadius: 8, flexShrink: 0, marginTop: 2,
                    background: `${phaseColor[step.phase] || '#86909c'}15`,
                    color: phaseColor[step.phase] || '#86909c',
                  }}>{phaseLabel[step.phase] || step.phase}</span>
                )}
                <div style={{ flex: 1, minWidth: 0 }}>
                  <span style={{ fontSize: 13, color: '#4e5969', lineHeight: 1.5 }}>{step.action}</span>
                  {step.expected && (
                    <div style={{ fontSize: 12, color: '#86909c', marginTop: 2 }}>预期: {step.expected}</div>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* 执行日志（始终展示） */}
      {executionLog && (
        <div>
          <div style={{ fontSize: 12, color: '#86909c', marginBottom: 6, fontWeight: 600 }}>执行日志</div>
          <pre style={{
            margin: 0, padding: '12px 14px', background: 'rgba(0,0,0,0.02)', color: '#4e5969',
            borderRadius: 12, fontSize: 12, lineHeight: 1.6, overflow: 'auto', maxHeight: 300,
            whiteSpace: 'pre-wrap', wordBreak: 'break-all', border: '1px solid rgba(0,0,0,0.04)',
            fontFamily: 'var(--font-mono)',
          }}>{executionLog}</pre>
        </div>
      )}

      {/* 预期结果 */}
      {expectedResult && (
        <div>
          <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4, fontWeight: 600 }}>预期结果</div>
          <div style={{ fontSize: 13, color: '#4e5969', padding: '8px 14px', background: 'var(--green-bg)', borderRadius: 12, border: '1px solid rgba(14,165,160,0.2)', lineHeight: 1.5 }}>
            {expectedResult}
          </div>
        </div>
      )}

      {/* 前置条件 */}
      {preconditions && (
        <div>
          <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4, fontWeight: 600 }}>前置条件</div>
          <div style={{ fontSize: 13, color: '#86909c', lineHeight: 1.6, whiteSpace: 'pre-wrap' }}>{preconditions}</div>
        </div>
      )}

      {!executionLog && !(caseSteps && caseSteps.length > 0) && (
        <div style={{ color: '#c9cdd4', fontSize: 13 }}>暂无执行详情</div>
      )}
    </div>
  )
}

export default function ReportDetail() {

  // 失败跟进单：这次报告里红的那些，各自跟进到哪了。
  // **放在报告里而不是单开一页** —— 人看完"这次红了 6 条"下一句话就是"那 6 条现在怎么样了"。
  const [tickets, setTickets] = useState([])
  const [closing, setClosing] = useState(null)
  const [closeReason, setCloseReason] = useState('')
  const [closeKnown, setCloseKnown] = useState(false)
  const navigate = useNavigate()
  const { projectId, reportId } = useParams()
  const [loading, setLoading] = useState(true)
  const [summary, setSummary] = useState(null)
  const [modules, setModules] = useState([])
  const [scenarios, setScenarios] = useState([])
  const [tab, setTab] = useState('all')
  const [keyword, setKeyword] = useState('')
  const [expandedIds, setExpandedIds] = useState(new Set())
  const [stepsCache, setStepsCache] = useState({})
  const [loadingSteps, setLoadingSteps] = useState({})
  const [selectedStep, setSelectedStep] = useState(null)
  const [exporting, setExporting] = useState(false)

  const fetchData = useCallback(async (silent = false) => {
    if (!projectId || !reportId) return
    if (!silent) setLoading(true)
    try {
      const [reportRes, resultsRes] = await Promise.all([
        api.get(`/projects/${projectId}/reports/${reportId}/dashboard`),
        api.get(`/projects/${projectId}/reports/${reportId}/results`),
      ])
      if (reportRes.data) {
        setSummary(reportRes.data.summary)
        setModules(reportRes.data.modules || [])
      }
      if (resultsRes.data) setScenarios(resultsRes.data.scenarios || [])
    } catch { /* */ } finally { if (!silent) setLoading(false) }
  }, [projectId, reportId])

  // branchId 组件级没有（只挂在每条场景上）。取第一条非空的 ——
  // 一份报告只属于一个分支，所以哪一条都一样。
  // 不取的话 loadTickets 里的 branchId 是 undefined，守卫直接 return，**静默不加载**。
  const branchId = useMemo(
    () => (scenarios || []).map(s => s.branchId).find(Boolean) || null, [scenarios])

  const loadTickets = useCallback(async () => {
    if (!projectId || !branchId || !reportId) return
    try {
      const r = await api.get(`/projects/${projectId}/branches/${branchId}/failure-tickets?reportId=${reportId}`)
      setTickets(r.data || [])
    } catch { /* request.js 已提示 */ }
  }, [projectId, branchId, reportId])

  useEffect(() => { loadTickets() }, [loadTickets])

  useEffect(() => { fetchData() }, [fetchData])

  // 执行中自动轮询（静默刷新，不触发 loading）
  const isRunning = summary && !summary.completedAt
  useEffect(() => {
    if (!isRunning) return
    const poll = setInterval(() => fetchData(true), 3000)
    return () => clearInterval(poll)
  }, [isRunning, fetchData])

  const loadSteps = async (scenarioId) => {
    if (stepsCache[scenarioId] || loadingSteps[scenarioId]) return
    setLoadingSteps(prev => ({ ...prev, [scenarioId]: true }))
    try {
      const res = await api.get(`/projects/${projectId}/reports/${reportId}/scenarios/${scenarioId}/steps`)
      setStepsCache(prev => ({ ...prev, [scenarioId]: res.data || [] }))
    } catch { /* */ } finally {
      setLoadingSteps(prev => ({ ...prev, [scenarioId]: false }))
    }
  }

  const toggleExpand = (id) => {
    setExpandedIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else { next.add(id); loadSteps(id) }
      return next
    })
  }

  const expandAll = () => {
    const autoIds = filtered.filter(s => s.executionType === 'automated').map(s => s.id)
    setExpandedIds(new Set(autoIds))
    autoIds.forEach(id => loadSteps(id))
  }

  const collapseAll = () => setExpandedIds(new Set())

  const handleExport = async () => {
    setExporting(true)
    try {
      const blob = await api.download(`/projects/${projectId}/reports/${reportId}/export/excel`)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = `report-${reportId}.xlsx`; a.click()
      URL.revokeObjectURL(url)
      message.success('导出成功')
    } catch { message.error('导出失败') } finally { setExporting(false) }
  }

  const filtered = useMemo(() => {
    let list = scenarios
    if (tab !== 'all') list = list.filter(s => s.status === tab)
    if (keyword) {
      const kw = keyword.toLowerCase()
      list = list.filter(s =>
        (s.scenarioName || '').toLowerCase().includes(kw) ||
        (s.caseCode || '').toLowerCase().includes(kw) ||
        (s.scriptRefFile || '').toLowerCase().includes(kw)
      )
    }
    return list
  }, [scenarios, tab, keyword])

  const counts = {}
  scenarios.forEach(s => { counts[s.status] = (counts[s.status] || 0) + 1 })
  const doneCount = scenarios.filter(s => s.status !== 'pending').length

  // 计算总耗时：如果 summary 没有，从 scenarios 汇总
  const totalDuration = summary?.totalDurationMs || scenarios.reduce((sum, s) => sum + (s.durationMs || 0), 0)

  if (loading) return <div style={{ textAlign: 'center', padding: 80 }}><Spin /></div>
  if (!summary) return <Empty description="暂无报告数据" />

  // 执行中时从 scenarios 实时计算统计数据
  const livePassed = isRunning ? (counts.passed || 0) : summary.passed
  const liveFailed = isRunning ? ((counts.failed || 0) + (counts.error || 0)) : (summary.failed + summary.error)
  const liveError = isRunning ? (counts.error || 0) : summary.error
  const liveSkipped = isRunning ? (counts.skipped || 0) : summary.skipped
  const liveFlaky = isRunning ? (counts.flaky || 0) : (summary.flaky || 0)
  const liveXfail = isRunning ? (counts.xfail || 0) : (summary.xfail || 0)
  const liveTotal = summary.totalScenarios
  // 两个百分比以前用的是**两个不同的分母**：通过率按 passed+failed+error 算、
  // 失败率按 totalScenarios 算，于是卡片上会出现「通过 2 (50.0%) / 失败 2 (28.6%)」
  // 这种加不到一起的数（实测截图为证），而且都和后端算的 passRate 对不上。
  // 统一成规范口径：passed + failed + error + flaky；skipped 和 xfail 不进分母。
  const denom = livePassed + liveFailed + liveFlaky
  const liveRate = doneCount > 0 && denom > 0 ? (livePassed / denom * 100).toFixed(1) : null
  // 分母为 0 时给 null 而不是 '0.0'：一条没跑的报告写「失败 0 (0.0%)」，
  // 读起来像"跑了、没失败"，其实是压根没跑。和上面通过率的「-」保持同一口径。
  const failRate = denom > 0 ? (liveFailed / denom * 100).toFixed(1) : null

  const renderScenarioRow = (s) => {
    const cfg = statusCfg[s.status] || statusCfg.pending
    const isExpanded = expandedIds.has(s.id)
    const steps = stepsCache[s.id]
    const isAutomatic = s.executionType === 'automated'
    const hasDetail = isAutomatic && (s.executionLog || (steps && steps.length > 0))

    return (
      <div key={s.id}>
        <div
          onClick={() => isAutomatic && toggleExpand(s.id)}
          style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            padding: '12px 20px',
            borderBottom: '1px solid rgba(0,0,0,0.04)',
            cursor: isAutomatic ? 'pointer' : 'default',
            background: isExpanded ? 'rgba(0,0,0,0.02)' : 'transparent',
            transition: 'background 0.15s',
          }}
          onMouseEnter={e => { if (isAutomatic) e.currentTarget.style.background = 'rgba(0,0,0,0.02)' }}
          onMouseLeave={e => { if (!isExpanded) e.currentTarget.style.background = 'transparent' }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, minWidth: 0 }}>
            <StatusDot status={s.status} />
            {isAutomatic && (
              <RightOutlined style={{
                fontSize: 11, color: '#c9cdd4', transition: 'transform 0.2s',
                transform: isExpanded ? 'rotate(90deg)' : 'none',
              }} />
            )}
            <span style={{ fontWeight: 500, fontSize: 13, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {s.scenarioName}
            </span>
            {s.scriptRefFile && (
              <span style={{ fontSize: 11, color: '#c9cdd4', fontFamily: 'var(--font-mono)', flexShrink: 0 }}>
                {s.scriptRefFile}{s.scriptRefFunc ? `::${s.scriptRefFunc}` : ''}
              </span>
            )}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0 }}>
            {s.remark && s.remark.includes('重试') && (
              <Tag style={{ color: '#faad14', border: 'none', background: 'transparent', fontSize: 11 }}>{s.remark}</Tag>
            )}
            {s.errorSummary && (
              // 这一列只有 200px，长一点的原因必然被截。不给 Tooltip 的话，
              // 后半句（往往正是"该去哪儿改"）就永远读不到了。
              <Tooltip title={s.errorSummary} styles={{ root: { maxWidth: 480 } }}>
                <span style={{ fontSize: 12, color: s.status === 'skipped' ? '#86909c' : '#e8453c', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', cursor: 'help' }}>
                  {s.errorSummary}
                </span>
              </Tooltip>
            )}
            {/* 已确认的原因盖过现象 —— 人确认过之后，现象就不是最该看的那一层了 */}
            {s.confirmedCause ? (
              <Tooltip title="人工已确认的失败原因">
                <Tag color="green" style={{ margin: 0, fontSize: 11 }}>
                  ✓ {causeLabel[s.confirmedCause] || s.confirmedCause}
                </Tag>
              </Tooltip>
            ) : s.phenomenon ? (
              <Tooltip title={s.ccAnalysis ? 'CC 已给出归因，展开可确认' : '平台按规则算的现象，展开可归因/确认'}>
                <Tag color={s.ccAnalysis ? 'blue' : 'orange'} style={{ margin: 0, fontSize: 11 }}>
                  {phenomenonLabel[s.phenomenon] || s.phenomenon}
                  {s.ccAnalysis && ' · 待确认'}
                </Tag>
              </Tooltip>
            ) : null}
            <Tag style={{ background: 'transparent', color: isAutomatic ? '#0ea5a0' : '#faad14', border: 'none', fontSize: 11 }}>
              {isAutomatic ? '自动' : '手动'}
            </Tag>
            {s.startedAt && (
              <span style={{ fontSize: 11, color: '#c9cdd4' }}>
                {new Date(s.startedAt).toLocaleTimeString('zh-CN')}
                {s.completedAt ? ` ~ ${new Date(s.completedAt).toLocaleTimeString('zh-CN')}` : ''}
              </span>
            )}
            <span style={{ fontSize: 13, color: '#86909c', fontFamily: 'var(--font-mono)', minWidth: 50, textAlign: 'right' }}>
              {fmt(s.durationMs)}
            </span>
          </div>
        </div>

        {isExpanded && (
          <div style={{ background: 'rgba(0,0,0,0.02)', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
            {loadingSteps[s.id] ? (
              <div style={{ textAlign: 'center', padding: 16 }}><Spin size="small" /></div>
            ) : steps && steps.length > 0 ? (
              steps.map(step => (
                <div key={step.id}
                  onClick={(e) => { e.stopPropagation(); setSelectedStep(step) }}
                  style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '7px 20px 7px 48px', borderBottom: '1px solid rgba(0,0,0,0.04)',
                    cursor: 'pointer', fontSize: 13, transition: 'background .12s',
                  }}
                  onMouseEnter={e => e.currentTarget.style.background = 'rgba(0,0,0,0.02)'}
                  onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8, flex: 1, minWidth: 0 }}>
                    <StatusDot status={step.status} />
                    {step.stepPhase && (
                      <span style={{
                        fontSize: 11, fontWeight: 600, padding: '0px 6px', borderRadius: 8, flexShrink: 0,
                        background: `${phaseColor[step.stepPhase] || '#86909c'}15`,
                        color: phaseColor[step.stepPhase] || '#86909c',
                      }}>{phaseLabel[step.stepPhase] || step.stepPhase}</span>
                    )}
                    {step.stepLabel ? (
                      <>
                        <span style={{ fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {step.stepLabel}
                        </span>
                        {step.httpMethod && (
                          <span style={{
                            fontSize: 11, fontWeight: 700, fontFamily: 'var(--font-mono)', flexShrink: 0,
                            padding: '0px 5px', borderRadius: 8,
                            background: `${methodColor[step.httpMethod] || '#86909c'}18`,
                            color: methodColor[step.httpMethod] || '#86909c',
                          }}>{step.httpMethod}</span>
                        )}
                        {stepRealUrl(step) && (
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 11, color: '#c9cdd4', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {stepRealUrl(step).replace(/^https?:\/\/[^/]+/, '')}
                          </span>
                        )}
                      </>
                    ) : (
                      <>
                        {step.httpMethod && (
                          <span style={{
                            fontSize: 11, fontWeight: 700, fontFamily: 'var(--font-mono)', flexShrink: 0,
                            padding: '1px 6px', borderRadius: 8,
                            background: `${methodColor[step.httpMethod] || '#86909c'}18`,
                            color: methodColor[step.httpMethod] || '#86909c',
                          }}>{step.httpMethod}</span>
                        )}
                        {stepRealUrl(step) && (
                          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: '#4e5969', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {stepRealUrl(step).replace(/^https?:\/\/[^/]+/, '')}
                          </span>
                        )}
                        {!stepRealUrl(step) && <span style={{ fontWeight: 500 }}>{step.stepName}</span>}
                      </>
                    )}
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexShrink: 0 }}>
                    {step.statusCode && (
                      <span style={{
                        fontFamily: 'var(--font-mono)', fontSize: 12, fontWeight: 600,
                        padding: '1px 6px', borderRadius: 8,
                        background: step.statusCode >= 400 ? '#fff2f0' : '#e0f7f6',
                        color: step.statusCode >= 400 ? '#e8453c' : '#0ea5a0',
                      }}>{step.statusCode}</span>
                    )}
                    <span style={{ fontSize: 12, color: '#c9cdd4', fontFamily: 'var(--font-mono)', minWidth: 48, textAlign: 'right' }}>
                      {fmt(step.durationMs)}
                    </span>
                    <RightOutlined style={{ fontSize: 11, color: '#c9cdd4' }} />
                  </div>
                </div>
              ))
            ) : (
              <ScenarioExpanded scenario={s} projectId={projectId} onConfirmed={() => fetchData(true)} />
            )}
          </div>
        )}
      </div>
    )
  }

  return (
    <div>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
        <Space size={8}>
          <Button type="text" icon={<ArrowLeftOutlined />} onClick={() => navigate(`/projects/${projectId}/reports`)} />
          <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0 }}>执行报告</h2>
        </Space>
        <Space>
          <Button icon={<SyncOutlined />} onClick={fetchData}>刷新</Button>
          <Button icon={<DownloadOutlined />} onClick={handleExport} loading={exporting}>导出报告</Button>
        </Space>
      </div>

      {/* L1 Summary Card - Centered */}
      <Card style={{ marginBottom: 8 }} styles={{ body: { padding: '32px 40px', display: 'flex', justifyContent: 'center' } }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 48 }}>
          <PassRateRing rate={liveRate} passed={livePassed} total={liveTotal} running={isRunning} done={doneCount} />

          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#0ea5a0', display: 'inline-block' }} />
              <span style={{ color: '#4e5969' }}>通过</span>
            </div>
            <div style={{ paddingLeft: 18, marginBottom: 8 }}>
              <span style={{ fontSize: 16, fontWeight: 600 }}>{livePassed}</span>
              <span style={{ color: '#86909c', marginLeft: 6 }}>({liveRate != null ? `${liveRate}%` : '-'})</span>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: '#e8453c', display: 'inline-block' }} />
              <span style={{ color: '#4e5969' }}>失败</span>
            </div>
            <div style={{ paddingLeft: 18 }}>
              <span style={{ fontSize: 16, fontWeight: 600 }}>{liveFailed}</span>
              <span style={{ color: '#86909c', marginLeft: 6 }}>({failRate != null ? `${failRate}%` : '-'})</span>
            </div>
          </div>

          <div style={{ borderLeft: '1px solid rgba(0,0,0,0.04)', paddingLeft: 40, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '16px 48px' }}>
            <div>
              <div style={{ color: '#86909c', fontSize: 13, marginBottom: 4 }}>总耗时</div>
              <div style={{ fontSize: 18, fontWeight: 600, color: '#0ea5a0' }}>{fmt(totalDuration) || '-'}</div>
            </div>
            <div>
              <div style={{ color: '#86909c', fontSize: 13, marginBottom: 4 }}>{isRunning ? '进度' : '总用例'}</div>
              {/* 「执行: N」以前直接印总用例数 —— 一条没跑、全被跳过的报告上写着
                  「执行: 2」，和旁边「跳过 2」当场打架。这一格标的是总数，
                  真跑了几条另说。 */}
              <div style={{ fontSize: 18, fontWeight: 600 }}>{isRunning ? `${doneCount} / ${liveTotal}` : liveTotal}</div>
            </div>
            <div>
              <div style={{ color: '#86909c', fontSize: 13, marginBottom: 4 }}>错误</div>
              <div style={{ fontSize: 18, fontWeight: 600, color: '#faad14' }}>{liveError}</div>
            </div>
            <div>
              <div style={{ color: '#86909c', fontSize: 13, marginBottom: 4 }}>跳过</div>
              <div style={{ fontSize: 18, fontWeight: 600, color: '#c9cdd4' }}>{liveSkipped}</div>
            </div>
            {/* 有才显示 —— 平时不占地方，出现了就必须看见：
                「重试后通过」算进通过率分母（它跑过但不可信），「预期失败」不算 */}
            {liveFlaky > 0 && (
              <div>
                <div style={{ color: '#86909c', fontSize: 13, marginBottom: 4 }}>重试后通过</div>
                <div style={{ fontSize: 18, fontWeight: 600, color: '#ff7d00' }}>{liveFlaky}</div>
              </div>
            )}
            {liveXfail > 0 && (
              <div>
                <div style={{ color: '#86909c', fontSize: 13, marginBottom: 4 }}>预期失败</div>
                <div style={{ fontSize: 18, fontWeight: 600, color: '#7c5cbf' }}>{liveXfail}</div>
              </div>
            )}
          </div>
        </div>
      </Card>

      {/* Filter Bar */}
      <Card style={{ marginBottom: 8 }} styles={{ body: { padding: '8px 16px' } }}>
        <div style={{ display: 'flex', alignItems: 'center' }}>
          <Space size={12}>
            <Radio.Group value={tab} onChange={e => setTab(e.target.value)} size="small" buttonStyle="solid">
              <Radio.Button value="all">全部 ({scenarios.length})</Radio.Button>
              {Object.entries(statusCfg).map(([k, v]) => counts[k] ? <Radio.Button key={k} value={k}><span style={{ color: v.color }}>{v.label} ({counts[k]})</span></Radio.Button> : null)}
            </Radio.Group>
            <Input
              prefix={<SearchOutlined style={{ color: '#c9cdd4' }} />}
              placeholder="搜索用例名称或编号"
              value={keyword}
              onChange={e => setKeyword(e.target.value)}
              allowClear
              size="small"
              style={{ width: 200 }}
            />
            <Tooltip title={expandedIds.size > 0 ? '全部收起' : '全部展开'}>
              <Button type="text" size="small" icon={
                <svg viewBox="0 0 1024 1024" width="14" height="14" fill="currentColor">
                  {expandedIds.size > 0 ? (
                    <><path d="M352 288l160 160 160-160" fill="none" stroke="currentColor" strokeWidth="80" strokeLinecap="round" strokeLinejoin="round"/>
                    <path d="M352 576l160 160 160-160" fill="none" stroke="currentColor" strokeWidth="80" strokeLinecap="round" strokeLinejoin="round"/></>
                  ) : (
                    <><path d="M352 448l160-160 160 160" fill="none" stroke="currentColor" strokeWidth="80" strokeLinecap="round" strokeLinejoin="round"/>
                    <path d="M352 736l160-160 160 160" fill="none" stroke="currentColor" strokeWidth="80" strokeLinecap="round" strokeLinejoin="round"/></>
                  )}
                </svg>
              } onClick={() => expandedIds.size > 0 ? collapseAll() : expandAll()} />
            </Tooltip>
          </Space>
        </div>
      </Card>

      {/* 失败跟进单 —— 这次红的那些现在到哪一步了 */}
      {tickets.length > 0 && (
        <Card style={{ marginBottom: 8 }} title={
          <span>失败跟进 <span style={{ fontSize: 12, color: '#86909c', marginLeft: 8 }}>
            这次红的 {tickets.length} 条，各自跟进到哪了。**跑绿会自动关单**；
            人工关必须写原因
          </span></span>
        } styles={{ body: { padding: '4px 0' } }}>
          {tickets.map(t => (
            <div key={t.id} style={{ display: 'flex', gap: 12, alignItems: 'flex-start',
              padding: '10px 16px', borderTop: '1px solid rgba(0,0,0,0.04)' }}>
              <Tag style={{ margin: 0, flexShrink: 0 }} color={{
                open: 'error', analyzed: 'warning', confirmed: 'processing',
                fixing: 'processing', verifying: 'warning', known: undefined,
              }[t.status]}>{{
                open: '待归因', analyzed: '待你确认', confirmed: '已确认待处置',
                fixing: '处置中', verifying: '待复跑', known: '已知问题', closed: '已关闭',
              }[t.status] || t.status}</Tag>
              <div style={{ flex: 1, fontSize: 13 }}>
                <div>
                  <span style={{ fontFamily: 'var(--font-mono)', fontSize: 12, color: '#86909c' }}>
                    {t.caseCode}</span>
                  <span style={{ marginLeft: 8 }}>{t.title}</span>
                </div>
                <div style={{ fontSize: 12, color: '#86909c', marginTop: 3 }}>
                  现象 {t.phenomenon} · 红了 {t.occurrences} 次
                  {t.recurrence ? ` · 第 ${t.recurrence} 次复发（之前关过又红了）` : ''}
                  {t.confirmedCause ? ` · 已确认：${t.confirmedCause}` : ''}
                </div>
                {t.ccAnalysis?.reason && (
                  <div style={{ fontSize: 12, color: '#4e5969', marginTop: 3 }}>
                    CC 归因：{t.ccAnalysis.reason}
                  </div>
                )}
              </div>
              <Button size="small" onClick={() => { setClosing(t); setCloseReason(''); setCloseKnown(false) }}>
                人工关闭
              </Button>
            </div>
          ))}
        </Card>
      )}

      {/* 人工关单：原因必填 —— 没有原因的关闭等于把红的问题从看板上抹掉 */}
      <Drawer title={`人工关闭 · ${closing?.caseCode || ''}`} open={!!closing} width={420}
        onClose={() => setClosing(null)}
        footer={
          <Space>
            <Button type="primary" disabled={closeReason.trim().length < 2}
              onClick={async () => {
                try {
                  await api.post(
                    `/projects/${projectId}/branches/${branchId}/failure-tickets/${closing.id}/close`,
                    { reason: closeReason.trim(), knownIssue: closeKnown })
                  message.success(closeKnown ? '已标为已知问题' : '已关闭')
                  setClosing(null); loadTickets()
                } catch (e) {
                  message.error(e?.response?.data?.error?.message || '关闭失败')
                }
              }}>确认关闭</Button>
            <Button onClick={() => setClosing(null)}>取消</Button>
          </Space>
        }>
        <div style={{ fontSize: 12, color: '#86909c', marginBottom: 10, lineHeight: 1.7 }}>
          正常的关单方式是**复跑跑绿**（平台自动关，并记下凭哪一次关的）。
          这里是强行关，所以原因必填 —— 下一轮它再冒出来时，人得看得懂上次为什么放过。
        </div>
        <Input.TextArea rows={4} value={closeReason} onChange={e => setCloseReason(e.target.value)}
          placeholder="为什么现在关掉它（必填）" />
        <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: 12, fontSize: 13 }}>
          <input type="checkbox" checked={closeKnown} onChange={e => setCloseKnown(e.target.checked)} />
          标成「已知问题」（知道它红、先不修；之后偶然绿一次也不自动关）
        </label>
      </Drawer>

      {/* Scenario List */}
      <Card styles={{ body: { padding: 0 } }}>
        {filtered.length === 0 ? (
          <div style={{ textAlign: 'center', padding: 40, color: '#c9cdd4' }}>暂无用例</div>
        ) : (
          filtered.map(renderScenarioRow)
        )}
      </Card>

      <StepDetailDrawer step={selectedStep} open={!!selectedStep} onClose={() => setSelectedStep(null)} />
    </div>
  )
}
