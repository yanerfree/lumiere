import { useState } from 'react'
import { Tag, Button, Space } from 'antd'
import {
  CheckCircleOutlined, CloseCircleOutlined, CloseOutlined,
  SendOutlined,
} from '@ant-design/icons'

const METHOD_COLORS = { GET: '#0ea5a0', POST: '#0ea5a0', PUT: '#faad14', DELETE: '#e8453c', PATCH: '#7c5cbf' }

function fmt(ms) {
  if (!ms && ms !== 0) return '-'
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function JsonBlock({ data }) {
  if (!data) return <span style={{ color: '#c9cdd4', fontSize: 12 }}>无数据</span>
  const text = typeof data === 'string' ? data : JSON.stringify(data, null, 2)
  return (
    <pre style={{
      fontSize: 12, lineHeight: 1.6, margin: 0, padding: '10px 12px',
      background: 'rgba(0,0,0,0.025)', borderRadius: 8, maxHeight: 300,
      overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
      fontFamily: 'var(--font-mono)',
      color: '#1d2129',
    }}>{text}</pre>
  )
}

function SectionTitle({ children }) {
  return (
    <div style={{
      fontSize: 12, fontWeight: 600, color: '#86909c',
      marginBottom: 8, display: 'flex', alignItems: 'center', gap: 6,
    }}>
      {children}
    </div>
  )
}

export default function StepRunDrawer({ response, stepName, onClose }) {
  if (!response) return null

  const isError = !!response.error
  const isOk = !isError && response.statusCode < 400
  const assertions = response.assertions || []
  const passCount = assertions.filter(a => a.passed).length
  const failCount = assertions.filter(a => a.passed === false).length
  const req = response.request

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Header */}
      <div style={{
        padding: '14px 20px', borderBottom: '1px solid rgba(0,0,0,0.06)', flexShrink: 0,
        background: isError ? 'rgba(232,69,60,0.03)' : isOk ? 'rgba(14,165,160,0.03)' : 'rgba(250,173,20,0.03)',
      }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Space size={8}>
            <SendOutlined style={{ color: '#0ea5a0' }} />
            <span style={{ fontWeight: 600, fontSize: 14 }}>{stepName || '运行结果'}</span>
          </Space>
          <Button type="text" size="small" icon={<CloseOutlined />} onClick={onClose} />
        </div>
        <div style={{ display: 'flex', gap: 12, marginTop: 8, alignItems: 'center' }}>
          {isError ? (
            <Tag color="error">错误</Tag>
          ) : (
            <>
              <Tag color={isOk ? 'cyan' : 'error'} style={{ fontSize: 13, fontWeight: 600, padding: '2px 10px' }}>
                {response.statusCode}
              </Tag>
              <span style={{ color: '#86909c', fontSize: 12 }}>{fmt(response.duration)}</span>
            </>
          )}
          {assertions.length > 0 && (
            <span style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
              {passCount > 0 && (
                <span style={{ color: '#0ea5a0', fontSize: 12, fontWeight: 500 }}>
                  <CheckCircleOutlined /> {passCount} 通过
                </span>
              )}
              {failCount > 0 && (
                <span style={{ color: '#e8453c', fontSize: 12, fontWeight: 500 }}>
                  <CloseCircleOutlined /> {failCount} 失败
                </span>
              )}
            </span>
          )}
        </div>
      </div>

      {/* Scrollable content */}
      <div style={{ flex: 1, overflow: 'auto', padding: '16px 20px' }}>
        {/* Error */}
        {isError && (
          <div style={{
            padding: '12px 16px', background: 'rgba(232,69,60,0.06)', borderRadius: 8,
            color: '#e8453c', fontSize: 13, marginBottom: 20, border: '1px solid rgba(232,69,60,0.12)',
          }}>
            {response.error}
          </div>
        )}

        {/* Assertions failures (prominent) */}
        {failCount > 0 && (
          <div style={{ marginBottom: 20 }}>
            <SectionTitle><CloseCircleOutlined style={{ color: '#e8453c' }} /> 断言失败</SectionTitle>
            <div style={{
              background: 'rgba(232,69,60,0.04)', borderRadius: 8,
              border: '1px solid rgba(232,69,60,0.1)', padding: '8px 12px',
            }}>
              {assertions.filter(a => a.passed === false).map((a, i) => (
                <div key={i} style={{ padding: '6px 0', display: 'flex', gap: 8, alignItems: 'center', fontSize: 12, borderBottom: i < failCount - 1 ? '1px solid rgba(0,0,0,0.04)' : 'none' }}>
                  <CloseCircleOutlined style={{ color: '#e8453c', flexShrink: 0 }} />
                  <span style={{ fontFamily: 'var(--font-mono)' }}>
                    {a.type === 'status' ? '状态码' : a.type === 'body_field' ? `字段 ${a.field}` : '包含文本'}
                    {' '}{a.operator}{' '}
                    <code style={{ background: 'rgba(0,0,0,0.06)', padding: '1px 6px', borderRadius: 3, fontWeight: 600 }}>
                      {typeof a.value === 'object' ? JSON.stringify(a.value) : String(a.value ?? '')}
                    </code>
                    {a.actual !== undefined && (
                      <span style={{ color: '#8c8c8c' }}>
                        {' '}→ 实际: <code style={{ background: 'rgba(0,0,0,0.06)', padding: '1px 6px', borderRadius: 3 }}>
                          {typeof a.actual === 'object' ? JSON.stringify(a.actual) : String(a.actual ?? '')}
                        </code>
                      </span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Request */}
        {req && (
          <div style={{ marginBottom: 20 }}>
            <SectionTitle><SendOutlined /> 请求</SectionTitle>
            {/* Method + URL */}
            <div style={{
              padding: '8px 12px', background: 'rgba(0,0,0,0.025)', borderRadius: 8,
              fontFamily: 'var(--font-mono)', fontSize: 12,
              display: 'flex', gap: 8, alignItems: 'flex-start', marginBottom: 10,
            }}>
              <Tag color={METHOD_COLORS[req.method] || '#86909c'} style={{ fontSize: 11, fontWeight: 600, flexShrink: 0 }}>
                {req.method}
              </Tag>
              <span style={{ wordBreak: 'break-all', color: '#1d2129', lineHeight: 1.6 }}>{req.url}</span>
            </div>

            {/* Headers */}
            {req.headers && Object.keys(req.headers).length > 0 && (
              <div style={{ marginBottom: 10 }}>
                <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4, fontWeight: 500 }}>Headers</div>
                <div style={{
                  background: 'rgba(0,0,0,0.025)', borderRadius: 8, padding: '6px 12px',
                  fontFamily: 'var(--font-mono)', fontSize: 12,
                }}>
                  {Object.entries(req.headers).map(([k, v]) => (
                    <div key={k} style={{ padding: '3px 0', display: 'flex', gap: 8 }}>
                      <span style={{ color: '#0ea5a0', fontWeight: 500, flexShrink: 0 }}>{k}:</span>
                      <span style={{ color: '#4e5969', wordBreak: 'break-all' }}>{String(v)}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Body */}
            {req.body && (
              <div>
                <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4, fontWeight: 500 }}>Body</div>
                <JsonBlock data={req.body} />
              </div>
            )}
          </div>
        )}

        {/* Response body */}
        {!isError && (
          <div style={{ marginBottom: 20 }}>
            <SectionTitle>响应</SectionTitle>
            <JsonBlock data={response.body} />
          </div>
        )}

        {/* All assertions (when all passed) */}
        {assertions.length > 0 && failCount === 0 && (
          <div style={{ marginBottom: 20 }}>
            <SectionTitle><CheckCircleOutlined style={{ color: '#0ea5a0' }} /> 断言全部通过</SectionTitle>
            <div style={{ background: 'rgba(14,165,160,0.04)', borderRadius: 8, padding: '8px 12px' }}>
              {assertions.map((a, i) => (
                <div key={i} style={{ padding: '4px 0', display: 'flex', gap: 8, alignItems: 'center', fontSize: 12 }}>
                  <CheckCircleOutlined style={{ color: '#0ea5a0', flexShrink: 0, fontSize: 12 }} />
                  <span style={{ fontFamily: 'var(--font-mono)' }}>
                    {a.type === 'status' ? `状态码 ${a.operator || '=='} ${a.value}` :
                     a.type === 'body_contains' ? `响应包含 "${a.value}"` :
                     a.type === 'body_field' ? `${a.field} ${a.operator || '=='} ${JSON.stringify(a.expected ?? a.value)}` :
                     JSON.stringify(a)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
