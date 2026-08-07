import { useState } from 'react'
import { Tag, Button, Space, Tooltip, Spin, message, Tabs } from 'antd'
import {
  CheckCircleOutlined, CloseCircleOutlined, CloseOutlined, LoadingOutlined,
  RightOutlined, DownOutlined, FileTextOutlined, CopyOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'

const METHOD_COLORS = { GET: '#0ea5a0', POST: '#0ea5a0', PUT: '#faad14', DELETE: '#e8453c', PATCH: '#7c5cbf' }
const MONO = "'SF Mono', Monaco, Consolas, monospace"

// 来源徽标配色：一眼区分「环境给的」「上游步骤提取的」「场景变量」
const SRC_COLOR = {
  env: '#0ea5a0', scenario_env: '#0ea5a0', scenario_var: '#7c5cbf',
  extract: '#1677ff', resource: '#fa8c16', runtime: '#86909c',
  auto_token: '#fa8c16', unknown: '#c9cdd4',
}

function fmt(ms) {
  if (!ms && ms !== 0) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function bodySize(body) {
  if (body == null) return '-'
  const n = new Blob([typeof body === 'string' ? body : JSON.stringify(body)]).size
  return n < 1024 ? `${n}B` : `${(n / 1024).toFixed(1)}KB`
}

function consoleCount(d) {
  return (d.request?.extracted?.length || 0) +
         (d.request?.variablesUsed || []).filter(v => v.source !== 'runtime').length
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

/** 控制台：变量的读与写，一行一条，像日志。
    不再摆成"来源说明"大表 —— 那太啰嗦，一屏看不完。来源压进同一行的尾注里。 */
function ConsoleLines({ used, extracted }) {
  const lines = []
  for (const x of extracted || []) {
    lines.push({
      key: `w-${x.name}`, ok: x.ok, op: '设置',
      text: <>已设置变量 <b>{x.name}</b> = <span style={{ color: x.ok ? '#0e7a76' : '#e8453c' }}>
        {x.ok ? String(x.value) : '取不到'}</span></>,
      note: `取自响应 ${x.path}`, copy: x.value,
    })
  }
  for (const v of used || []) {
    if (v.source === 'runtime') continue          // 平台自注入的噪音，不用刷屏
    lines.push({
      key: `r-${v.name}`, ok: v.value != null, op: '使用',
      text: <>使用变量 <b>{v.name}</b> = <span style={{ color: v.value == null ? '#e8453c' : '#0e7a76' }}>
        {v.value == null ? '未解析' : String(v.value)}</span></>,
      note: v.detail, copy: v.value,
    })
  }
  if (!lines.length) return <div style={{ fontSize: 12, color: '#c9cdd4', padding: '8px 2px' }}>本步没有变量读写</div>
  return (
    <div style={{ fontFamily: MONO, fontSize: 11 }}>
      {lines.map(l => (
        <div key={l.key} style={{
          display: 'flex', gap: 6, padding: '5px 6px', alignItems: 'flex-start',
          borderBottom: '1px solid rgba(0,0,0,0.04)',
          background: l.ok ? 'transparent' : 'rgba(232,69,60,0.05)',
        }}>
          <Tag style={{ margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 5px', flexShrink: 0 }}
            color={l.op === '设置' ? '#1677ff' : '#86909c'}>{l.op}</Tag>
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
  const desc = (a) =>
    a.type === 'status' ? `状态码 ${a.operator || '=='} ${JSON.stringify(a.value)}（实际 ${statusCode}）` :
    a.type === 'body_contains' ? `响应${a.operator === 'not_contains' ? '不' : ''}包含 ${JSON.stringify(a.value)}` :
    a.type === 'body_field' ? `${a.field} ${a.operator || '=='} ${JSON.stringify(a.expected ?? a.value)}${a.actual !== undefined ? `（实际 ${JSON.stringify(a.actual)}）` : ''}` :
    JSON.stringify(a)
  return (
    <div>
      {items.map((a, j) => (
        <div key={j} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 11, padding: '3px 0' }}>
          <span style={{ color: '#86909c', minWidth: 14 }}>{j + 1}.</span>
          {a.passed ? <CheckCircleOutlined style={{ color: '#0ea5a0', fontSize: 12, marginTop: 2 }} />
                    : <CloseCircleOutlined style={{ color: '#e8453c', fontSize: 12, marginTop: 2 }} />}
          <span style={{ fontFamily: MONO, wordBreak: 'break-all' }}>{desc(a)}</span>
        </div>
      ))}
    </div>
  )
}

/** 实际请求：真正发出去的那一份 */
function ActualRequest({ req }) {
  if (!req) return <div style={{ fontSize: 12, color: '#c9cdd4' }}>无请求数据</div>
  return (
    <div style={{ fontSize: 11 }}>
      <div style={{ fontFamily: MONO, wordBreak: 'break-all', marginBottom: 6 }}>
        <Tag color={METHOD_COLORS[req.method]} style={{ fontSize: 10, lineHeight: '16px', padding: '0 5px' }}>{req.method}</Tag>
        {req.url}<CopyBtn text={req.url} label="URL" />
        {req.urlTemplate && req.urlTemplate !== req.url && (
          <div style={{ color: '#c9cdd4', marginTop: 2 }}>模板 {req.urlTemplate}</div>
        )}
      </div>
      {req.authOrigin && (
        <div style={{ color: '#86909c', marginBottom: 6 }}>Authorization —— {req.authOrigin}</div>
      )}
      {req.headers && Object.keys(req.headers).length > 0 && (
        <div style={{ fontFamily: MONO, padding: '6px 8px', background: 'rgba(0,0,0,0.03)', borderRadius: 6, marginBottom: 6 }}>
          {Object.entries(req.headers).map(([k, v]) => (
            <div key={k} style={{ display: 'flex', gap: 6, wordBreak: 'break-all', padding: '1px 0' }}>
              <span style={{ color: '#86909c', flexShrink: 0 }}>{k}:</span>
              <span style={{ flex: 1 }}>{String(v)}</span>
              <CopyBtn text={v} label={k} />
            </div>
          ))}
        </div>
      )}
      {req.body != null && <JsonBlock data={req.body} max={220} />}
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
                .map(a => a.type === 'status' ? `状态码期望 ${a.operator || '=='} ${JSON.stringify(a.value)}，实际 ${r.statusCode}` : `断言未通过: ${a.type}`)
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

                <Tag color={METHOD_COLORS[r.method]} style={{ fontSize: 10, margin: 0, padding: '0 4px', lineHeight: '18px' }}>
                  {r.method || 'GET'}
                </Tag>

                <span style={{ flex: 1, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {r.stepName}
                </span>

                <span style={{ fontSize: 11, color: '#c9cdd4', flexShrink: 0 }}>{fmt(r.duration)}</span>

                {isExpanded ? <DownOutlined style={{ fontSize: 10, color: '#c9cdd4' }} /> :
                              <RightOutlined style={{ fontSize: 10, color: '#c9cdd4' }} />}
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
                    <div style={{ padding: '6px 10px', background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: 6, fontSize: 11, color: '#e8453c', whiteSpace: 'pre-wrap', marginBottom: 8 }}>
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
                        children: <ConsoleLines used={detail.request?.variablesUsed} extracted={detail.request?.extracted} />,
                      },
                      { key: 'req', label: '实际请求', children: <ActualRequest req={detail.request} /> },
                      ...(detail.request?.preScript || detail.request?.postScript ? [{
                        key: 'script', label: '脚本',
                        children: (
                          <div>
                            {detail.request?.preScript && (<><SectionTitle>前置</SectionTitle><JsonBlock data={detail.request.preScript} max={160} /></>)}
                            {detail.request?.postScript && (<><SectionTitle>后置</SectionTitle><JsonBlock data={detail.request.postScript} max={160} /></>)}
                          </div>
                        ),
                      }] : []),
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
