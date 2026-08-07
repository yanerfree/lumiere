import { useState } from 'react'
import { Tag, Button, Space, Tooltip, Spin, message } from 'antd'
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

/** 这一步实际用到的每个 ${变量}：真实取值 + 从哪来。 */
function VariablesUsed({ vars }) {
  if (!vars?.length) return null
  return (
    <div>
      <SectionTitle extra={`${vars.length} 个`}>变量取值与来源</SectionTitle>
      <div style={{ border: '1px solid rgba(0,0,0,0.06)', borderRadius: 6, overflow: 'hidden' }}>
        {vars.map((v, i) => (
          <div key={v.name} style={{
            padding: '6px 8px', fontSize: 11,
            borderTop: i ? '1px solid rgba(0,0,0,0.04)' : 'none',
            background: v.value == null ? 'rgba(232,69,60,0.05)' : 'transparent',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              <span style={{ fontFamily: MONO, fontWeight: 600, color: '#1d2129' }}>${'{'}{v.name}{'}'}</span>
              <Tag style={{ margin: 0, fontSize: 10, lineHeight: '16px', padding: '0 5px' }}
                color={SRC_COLOR[v.source] || SRC_COLOR.unknown}>{v.sourceLabel}</Tag>
              <CopyBtn text={v.value} label={v.name} />
            </div>
            <div style={{ fontFamily: MONO, color: v.value == null ? '#e8453c' : '#0e7a76', wordBreak: 'break-all', marginTop: 2 }}>
              = {v.value == null ? '未解析（无来源）' : String(v.value)}
            </div>
            <div style={{ color: '#86909c', marginTop: 2 }}>{v.detail}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

/** 本步从响应里提取了什么，给后面的步骤用。 */
function Extracted({ items }) {
  if (!items?.length) return null
  return (
    <div>
      <SectionTitle extra={`${items.filter(x => x.ok).length}/${items.length} 成功`}>提取变量（供后续步骤引用）</SectionTitle>
      <div style={{ border: '1px solid rgba(0,0,0,0.06)', borderRadius: 6, overflow: 'hidden' }}>
        {items.map((x, i) => (
          <div key={x.name} style={{
            padding: '6px 8px', fontSize: 11,
            borderTop: i ? '1px solid rgba(0,0,0,0.04)' : 'none',
            background: x.ok ? 'transparent' : 'rgba(232,69,60,0.05)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
              {x.ok ? <CheckCircleOutlined style={{ color: '#0ea5a0', fontSize: 11 }} />
                    : <CloseCircleOutlined style={{ color: '#e8453c', fontSize: 11 }} />}
              <span style={{ fontFamily: MONO, fontWeight: 600 }}>${'{'}{x.name}{'}'}</span>
              <span style={{ color: '#86909c' }}>← 响应 {x.path}</span>
              <CopyBtn text={x.value} label={x.name} />
            </div>
            <div style={{ fontFamily: MONO, color: x.ok ? '#0e7a76' : '#e8453c', wordBreak: 'break-all', marginTop: 2 }}>
              = {x.ok ? String(x.value) : '取不到（检查 JSONPath 或响应结构）'}
            </div>
          </div>
        ))}
      </div>
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
                <div style={{ padding: '8px 16px 14px 28px', background: 'rgba(0,0,0,0.02)', borderBottom: '1px solid rgba(0,0,0,0.04)' }}>
                  {/* 请求 —— URL 是实际发出的那个；模板另起一行，两个都要看得到 */}
                  {detail.request && (
                    <div>
                      <SectionTitle>请求</SectionTitle>
                      <div style={{ fontSize: 12, fontFamily: MONO, wordBreak: 'break-all' }}>
                        <Tag color={METHOD_COLORS[detail.request.method]} style={{ fontSize: 11 }}>{detail.request.method}</Tag>
                        {detail.request.url}
                        <CopyBtn text={detail.request.url} label="URL" />
                      </div>
                      {detail.request.urlTemplate && detail.request.urlTemplate !== detail.request.url && (
                        <div style={{ fontSize: 11, color: '#c9cdd4', fontFamily: MONO, marginTop: 2 }}>
                          模板：{detail.request.urlTemplate}
                        </div>
                      )}

                      {detail.request.headers && Object.keys(detail.request.headers).length > 0 && (
                        <>
                          <SectionTitle extra={detail.request.authOrigin ? `Authorization ${detail.request.authOrigin}` : null}>
                            请求头
                          </SectionTitle>
                          <div style={{ fontSize: 11, fontFamily: MONO, padding: '6px 8px', background: 'rgba(0,0,0,0.03)', borderRadius: 6 }}>
                            {Object.entries(detail.request.headers).map(([k, v]) => (
                              <div key={k} style={{ display: 'flex', gap: 6, wordBreak: 'break-all', padding: '1px 0' }}>
                                <span style={{ color: '#86909c', flexShrink: 0 }}>{k}:</span>
                                <span style={{ flex: 1 }}>{String(v)}</span>
                                <CopyBtn text={v} label={k} />
                              </div>
                            ))}
                          </div>
                        </>
                      )}

                      {detail.request.params && Object.keys(detail.request.params).length > 0 && (
                        <><SectionTitle>Query 参数</SectionTitle><JsonBlock data={detail.request.params} /></>
                      )}
                      {detail.request.body != null && (
                        <><SectionTitle>请求体</SectionTitle><JsonBlock data={detail.request.body} /></>
                      )}
                    </div>
                  )}

                  {/* 变量取值与来源 —— 「这个 id 哪来的」在这里回答 */}
                  <VariablesUsed vars={detail.request?.variablesUsed} />

                  {/* 前置 / 后置脚本 */}
                  {detail.request?.preScript && (
                    <><SectionTitle>前置脚本</SectionTitle><JsonBlock data={detail.request.preScript} max={160} /></>
                  )}

                  {/* 响应 */}
                  <SectionTitle extra={fmt(detail.duration)}>
                    响应
                    {detail.statusCode != null && (
                      <Tag color={detail.statusCode < 400 ? '#0ea5a0' : '#e8453c'} style={{ marginLeft: 8, fontSize: 10, lineHeight: '16px', padding: '0 5px' }}>
                        {detail.statusCode}
                      </Tag>
                    )}
                  </SectionTitle>
                  {detail.error ? (
                    <div style={{ padding: '6px 10px', background: '#fff2f0', border: '1px solid #ffccc7', borderRadius: 6, fontSize: 12, color: '#e8453c', whiteSpace: 'pre-wrap' }}>
                      {detail.error}
                    </div>
                  ) : (
                    <JsonBlock data={detail.body} max={300} />
                  )}

                  {/* 断言 —— 期望与实际都写出来 */}
                  {detail.assertions?.length > 0 && (
                    <>
                      <SectionTitle extra={`${detail.assertions.filter(a => a.passed).length}/${detail.assertions.length} 通过`}>断言</SectionTitle>
                      {detail.assertions.map((a, j) => (
                        <div key={j} style={{ display: 'flex', alignItems: 'flex-start', gap: 6, fontSize: 11, padding: '2px 0' }}>
                          {a.passed ? <CheckCircleOutlined style={{ color: '#0ea5a0', fontSize: 12, marginTop: 3 }} /> :
                                      <CloseCircleOutlined style={{ color: '#e8453c', fontSize: 12, marginTop: 3 }} />}
                          <span style={{ fontFamily: MONO, wordBreak: 'break-all' }}>
                            {a.type === 'status' ? `状态码 ${a.operator || '=='} ${JSON.stringify(a.value)}（实际 ${detail.statusCode}）` :
                             a.type === 'body_contains' ? `响应${a.operator === 'not_contains' ? '不' : ''}包含 ${JSON.stringify(a.value)}` :
                             a.type === 'body_field' ? `${a.field} ${a.operator || '=='} ${JSON.stringify(a.expected ?? a.value)}${a.actual !== undefined ? `（实际 ${JSON.stringify(a.actual)}）` : ''}` :
                             JSON.stringify(a)}
                          </span>
                        </div>
                      ))}
                    </>
                  )}

                  {/* 提取变量 */}
                  <Extracted items={detail.request?.extracted} />

                  {detail.request?.postScript && (
                    <><SectionTitle>后置脚本</SectionTitle><JsonBlock data={detail.request.postScript} max={160} /></>
                  )}
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
