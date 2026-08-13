import { useState, useEffect } from 'react'
import { Button, Tag, Space, Input, Select, Tabs, Popconfirm, Typography, message , InputNumber} from 'antd'
import {
  DeleteOutlined, CaretRightOutlined, LoadingOutlined,
  CheckCircleOutlined, CloseCircleOutlined, SendOutlined, PlusOutlined,
} from '@ant-design/icons'

const { Text } = Typography
const cellStyle = { padding: '6px 12px', borderBottom: '1px solid rgba(0,0,0,0.04)' }
const thStyle = { ...cellStyle, fontWeight: 500, background: 'rgba(255,255,255,0.3)', textAlign: 'left' }

function EditableHeadersTable({ headers, onSave, readonly }) {
  const entries = headers ? Object.entries(headers) : []
  const [rows, setRows] = useState(entries.map(([k, v]) => ({ key: k, value: v })))

  useEffect(() => {
    const newEntries = headers ? Object.entries(headers) : []
    setRows(newEntries.map(([k, v]) => ({ key: k, value: v })))
  }, [headers])

  const save = (updated) => {
    const obj = {}
    updated.forEach(r => { if (r.key.trim()) obj[r.key.trim()] = r.value })
    onSave(obj)
  }

  return (
    <div style={{ background: 'transparent', borderRadius: 8, border: '1px solid rgba(0,0,0,0.06)', overflow: 'hidden' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>
            <th style={thStyle}>Key</th>
            <th style={thStyle}>Value</th>
            {!readonly && <th style={{ ...thStyle, width: 40 }}></th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              <td style={cellStyle}>
                <Input size="small" variant="borderless" value={row.key} disabled={readonly} style={{ fontWeight: 500 }}
                  onChange={e => { const r = [...rows]; r[i] = { ...r[i], key: e.target.value }; setRows(r) }}
                  onBlur={() => save(rows)} />
              </td>
              <td style={cellStyle}>
                <Input spellCheck={false} size="small" variant="borderless" value={row.value} disabled={readonly} style={{ fontFamily: 'var(--font-mono)', color: '#4e5969' }}
                  onChange={e => { const r = [...rows]; r[i] = { ...r[i], value: e.target.value }; setRows(r) }}
                  onBlur={() => save(rows)} />
              </td>
              {!readonly && (
                <td style={cellStyle}>
                  <Button type="text" size="small" danger icon={<DeleteOutlined />}
                    onClick={() => { const r = rows.filter((_, j) => j !== i); setRows(r); save(r) }} />
                </td>
              )}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={readonly ? 2 : 3} style={{ padding: 12, color: '#bfbfbf', textAlign: 'center' }}>无自定义 Headers</td></tr>
          )}
        </tbody>
      </table>
      {!readonly && (
        <div style={{ padding: '4px 12px', borderTop: '1px solid rgba(0,0,0,0.04)' }}>
          <Button type="text" size="small" icon={<PlusOutlined />} style={{ fontSize: 11, color: '#8c8c8c' }}
            onClick={() => { setRows([...rows, { key: '', value: '' }]) }}>
            添加
          </Button>
        </div>
      )}
    </div>
  )
}

function EditableAssertionsTable({ assertions, onSave, readonly }) {
  const [rows, setRows] = useState(assertions || [])

  useEffect(() => { setRows(assertions || []) }, [assertions])

  const save = (updated) => onSave(updated)

  return (
    <div style={{ background: 'transparent', borderRadius: 8, border: '1px solid rgba(0,0,0,0.06)', overflow: 'hidden' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>
            <th style={{ ...thStyle, width: 30 }}></th>
            <th style={thStyle}>类型</th>
            <th style={thStyle}>字段</th>
            <th style={thStyle}>操作</th>
            <th style={thStyle}>期望值</th>
            {!readonly && <th style={{ ...thStyle, width: 40 }}></th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((a, j) => (
            <tr key={j}>
              <td style={cellStyle}>
                {a.passed === true ? <CheckCircleOutlined style={{ color: '#0ea5a0' }} /> :
                 a.passed === false ? <CloseCircleOutlined style={{ color: '#e8453c' }} /> :
                 <span style={{ color: '#d9d9d9' }}>○</span>}
              </td>
              <td style={cellStyle}>
                <Select size="small" variant="borderless" value={a.type} style={{ width: '100%' }}
                  disabled={readonly}
                  options={[
                    { value: 'status', label: '状态码' },
                    { value: 'body_field', label: 'JSON字段' },
                    { value: 'body_contains', label: '包含文本' },
                  ]}
                  onChange={v => { const r = [...rows]; r[j] = { ...r[j], type: v }; setRows(r); save(r) }} />
              </td>
              <td style={cellStyle}>
                <Input spellCheck={false} size="small" variant="borderless" value={a.field || ''} style={{ fontFamily: 'var(--font-mono)', color: '#4e5969' }}
                  disabled={readonly}
                  placeholder={a.type === 'status' ? '-' : 'data.id'}
                  onChange={e => { const r = [...rows]; r[j] = { ...r[j], field: e.target.value }; setRows(r) }}
                  onBlur={() => save(rows)} />
              </td>
              <td style={cellStyle}>
                <Select size="small" variant="borderless" value={a.operator} style={{ width: '100%' }}
                  disabled={readonly}
                  options={[
                    { value: '==', label: '==' },
                    { value: '!=', label: '!=' },
                    { value: '>', label: '>' },
                    { value: '<', label: '<' },
                    { value: 'contains', label: '包含' },
                    { value: 'not_contains', label: '不包含' },
                    { value: 'not_empty', label: '非空' },
                  ]}
                  onChange={v => { const r = [...rows]; r[j] = { ...r[j], operator: v }; setRows(r); save(r) }} />
              </td>
              <td style={cellStyle}>
                <Input size="small" variant="borderless" value={typeof a.value === 'object' ? JSON.stringify(a.value) : String(a.value ?? '')}
                  disabled={readonly}
                  onChange={e => {
                    let val = e.target.value
                    try { val = JSON.parse(val) } catch { /* keep string */ }
                    const r = [...rows]; r[j] = { ...r[j], value: val }; setRows(r)
                  }}
                  onBlur={() => save(rows)} />
              </td>
              {!readonly && (
                <td style={cellStyle}>
                  <Button type="text" size="small" danger icon={<DeleteOutlined />}
                    onClick={() => { const r = rows.filter((_, k) => k !== j); setRows(r); save(r) }} />
                </td>
              )}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={readonly ? 5 : 6} style={{ padding: 12, color: '#bfbfbf', textAlign: 'center' }}>无断言</td></tr>
          )}
        </tbody>
      </table>
      {!readonly && (
        <div style={{ padding: '4px 12px', borderTop: '1px solid rgba(0,0,0,0.04)' }}>
          <Button type="text" size="small" icon={<PlusOutlined />} style={{ fontSize: 11, color: '#8c8c8c' }}
            onClick={() => { setRows([...rows, { type: 'status', operator: '==', value: 200 }]) }}>
            添加断言
          </Button>
        </div>
      )}
    </div>
  )
}

function EditableVariablesTable({ variables, onSave, readonly }) {
  const entries = variables ? Object.entries(variables) : []
  const [rows, setRows] = useState(entries.map(([k, v]) => ({ key: k, value: v })))

  useEffect(() => {
    const newEntries = variables ? Object.entries(variables) : []
    setRows(newEntries.map(([k, v]) => ({ key: k, value: v })))
  }, [variables])

  const save = (updated) => {
    const obj = {}
    updated.forEach(r => { if (r.key.trim()) obj[r.key.trim()] = r.value })
    onSave(obj)
  }

  return (
    <div style={{ background: 'transparent', borderRadius: 8, border: '1px solid rgba(0,0,0,0.06)', overflow: 'hidden' }}>
      <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
        <thead>
          <tr>
            <th style={thStyle}>变量名</th>
            <th style={thStyle}>JSONPath</th>
            <th style={{ ...thStyle, width: 40 }}></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i}>
              <td style={cellStyle}>
                <Input spellCheck={false} size="small" variant="borderless" value={row.key}
                  disabled={readonly}
                  style={{ color: '#d46b08', fontWeight: 500, fontFamily: 'var(--font-mono)' }}
                  placeholder="变量名"
                  onChange={e => { const r = [...rows]; r[i] = { ...r[i], key: e.target.value }; setRows(r) }}
                  onBlur={() => save(rows)} />
              </td>
              <td style={cellStyle}>
                <Input spellCheck={false} size="small" variant="borderless" value={row.value}
                  disabled={readonly}
                  style={{ fontFamily: 'var(--font-mono)' }}
                  placeholder="data.token"
                  onChange={e => { const r = [...rows]; r[i] = { ...r[i], value: e.target.value }; setRows(r) }}
                  onBlur={() => save(rows)} />
              </td>
              {!readonly && (
                <td style={cellStyle}>
                  <Button type="text" size="small" danger icon={<DeleteOutlined />}
                    onClick={() => { const r = rows.filter((_, j) => j !== i); setRows(r); save(r) }} />
                </td>
              )}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr><td colSpan={readonly ? 2 : 3} style={{ padding: 12, color: '#bfbfbf', textAlign: 'center' }}>无变量提取</td></tr>
          )}
        </tbody>
      </table>
      {!readonly && (
        <div style={{ padding: '4px 12px', borderTop: '1px solid rgba(0,0,0,0.04)' }}>
          <Button type="text" size="small" icon={<PlusOutlined />} style={{ fontSize: 11, color: '#8c8c8c' }}
            onClick={() => { setRows([...rows, { key: '', value: '' }]) }}>
            添加变量
          </Button>
        </div>
      )}
      <div style={{ padding: '4px 12px', fontSize: 11, color: '#8c8c8c' }}>
        从响应中提取变量，后续步骤可用 ${'{变量名}'} 引用
      </div>
    </div>
  )
}

export default function StepEditor({
  step, running, readonly,
  onSaveStep, onRemoveStep, onRunStep,
  onStepChange,
}) {
  const [bodyText, setBodyText] = useState('')
  const [activeTab, setActiveTab] = useState('body')

  useEffect(() => {
    setBodyText(step?.body ? JSON.stringify(step.body, null, 2) : '')
  }, [step?.id])

  useEffect(() => {
    setActiveTab('body')
  }, [step?.id])

  const handleBodyBlur = () => {
    try {
      const parsed = JSON.parse(bodyText || '{}')
      onSaveStep(step.id, { body: parsed })
    } catch { /* JSON 不合法时不保存，用户继续编辑 */ }
  }

  if (!step) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#bfbfbf' }}>
        <div style={{ textAlign: 'center' }}>
          <SendOutlined style={{ fontSize: 40, marginBottom: 12 }} />
          <div style={{ fontSize: 13 }}>选择左侧步骤查看请求详情</div>
        </div>
      </div>
    )
  }

  return (
    <>
      <div style={{ padding: '10px 20px', borderBottom: '1px solid rgba(0,0,0,0.04)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <Input
          value={step.name}
          variant="borderless"
          disabled={readonly}
          style={{ fontWeight: 600, fontSize: 14, flex: 1, padding: 0 }}
          onBlur={e => onSaveStep(step.id, { name: e.target.value })}
          onChange={e => onStepChange({ ...step, name: e.target.value })}
        />
        <Space size={4}>
          {!readonly && (
            <Popconfirm title="删除此步骤？" onConfirm={() => onRemoveStep(step.id)}>
              <Button size="small" type="text" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
          <Button
            type="primary"
            icon={running ? <LoadingOutlined /> : <CaretRightOutlined />}
            loading={running}
            onClick={onRunStep}
            style={{ fontWeight: 500 }}
          >
            运行
          </Button>
        </Space>
      </div>

      <div style={{ padding: '10px 20px', borderBottom: '1px solid rgba(0,0,0,0.04)', display: 'flex', gap: 8, alignItems: 'center' }}>
        <Select value={step.method} size="small"
          disabled={readonly}
          style={{ width: 90, fontWeight: 600 }}
          onChange={v => { onStepChange({ ...step, method: v }); onSaveStep(step.id, { method: v }) }}
          options={['GET','POST','PUT','DELETE','PATCH'].map(m => ({ value: m, label: m }))}
        />
        <Input
          value={step.url}
          variant="borderless"
          disabled={readonly}
          style={{ fontFamily: 'var(--font-mono)', fontSize: 13, color: '#1d2129' }}
          onChange={e => onStepChange({ ...step, url: e.target.value })}
          onBlur={e => onSaveStep(step.id, { url: e.target.value })}
        />
        <Button size="small" style={{ fontSize: 12 }} onClick={onRunStep}>发送</Button>
      </div>

      <div style={{ flex: 1, overflow: 'auto' }}>
        <Tabs
          activeKey={activeTab}
          onChange={setActiveTab}
          size="small"
          style={{ padding: '0 20px' }}
          items={[
            {
              key: 'body',
              label: <span>Body {step.body && <span style={{ color: '#0ea5a0' }}>●</span>}</span>,
              children: (
                <div style={{ background: 'transparent', borderRadius: 8, border: '1px solid rgba(0,0,0,0.06)', overflow: 'hidden' }}>
                  <div style={{ padding: '6px 12px', background: 'rgba(255,255,255,0.3)', borderBottom: '1px solid rgba(0,0,0,0.04)', fontSize: 11, color: '#8c8c8c' }}>
                    <span>JSON</span>
                  </div>
                  <textarea
                    value={bodyText}
                    onChange={e => setBodyText(e.target.value)}
                    onBlur={handleBodyBlur}
                    readOnly={readonly}
                    style={{
                      width: '100%', border: 'none', outline: 'none', resize: 'vertical',
                      padding: 16, fontSize: 13, fontFamily: 'var(--font-mono)',
                      lineHeight: 1.6, minHeight: 100, maxHeight: 400, color: '#1d2129', background: 'transparent',
                    }}
                  />
                </div>
              ),
            },
            {
              key: 'headers',
              label: <span>Headers {step.headers && Object.keys(step.headers).length > 0 && <Tag style={{ fontSize: 10, padding: '0 4px', lineHeight: '16px' }}>{Object.keys(step.headers).length}</Tag>}</span>,
              children: (
                <EditableHeadersTable
                  headers={step.headers}
                  readonly={readonly}
                  onSave={h => onSaveStep(step.id, { headers: h })}
                />
              ),
            },
            {
              key: 'assertions',
              label: <span>断言 {step.assertions?.length > 0 && <Tag color="#0ea5a0" style={{ fontSize: 10, padding: '0 4px', lineHeight: '16px' }}>{step.assertions.length}</Tag>}</span>,
              children: (
                <EditableAssertionsTable
                  assertions={step.assertions}
                  readonly={readonly}
                  onSave={a => onSaveStep(step.id, { assertions: a })}
                />
              ),
            },
            {
              key: 'variables',
              label: <span>变量提取 {step.variablesExtract && Object.keys(step.variablesExtract).length > 0 && <Tag style={{ fontSize: 10, padding: '0 4px', lineHeight: '16px' }}>{Object.keys(step.variablesExtract).length}</Tag>}</span>,
              children: (
                <EditableVariablesTable
                  variables={step.variablesExtract}
                  readonly={readonly}
                  onSave={v => onSaveStep(step.id, { variablesExtract: v })}
                />
              ),
            },
            {
              key: 'timing',
              label: <span>等待/重试 {(step.retryTimeoutMs > 0 || step.waitMs > 0) && <Tag color="#fa8c16" style={{ fontSize: 10, padding: '0 4px', lineHeight: '16px' }}>已设</Tag>}</span>,
              children: (
                <div style={{ padding: '4px 2px', fontSize: 12, color: '#4e5969', lineHeight: 1.9 }}>
                  {/* 不做成隐形魔法：一步为什么慢、为什么重试，得在页面上看得见 */}
                  <div style={{ marginBottom: 10, color: '#86909c' }}>
                    被测系统的配置下发常是异步的（实测网关 0.06~0.5s 且抖动），而步骤之间只隔几毫秒 ——
                    「发布完立刻打网关」会抢跑，跑出来是红的，但那不是缺陷。
                    <b style={{ color: '#4e5969' }}>优先用重试，别用固定等待</b>：固定等待要么白等要么不够，换台机器就崩。
                  </div>
                  <Space size={16} wrap>
                    <span>发请求前先等
                      <InputNumber size="small" min={0} max={60000} step={100} disabled={readonly}
                        value={step.waitMs || 0} style={{ width: 96, margin: '0 4px' }}
                        onChange={v => onSaveStep(step.id, { waitMs: v || 0 })} /> ms</span>
                    <span>断言没过就重发，最多等
                      <InputNumber size="small" min={0} max={120000} step={500} disabled={readonly}
                        value={step.retryTimeoutMs || 0} style={{ width: 104, margin: '0 4px' }}
                        onChange={v => onSaveStep(step.id, { retryTimeoutMs: v || 0 })} /> ms（0=不重试）</span>
                    <span>每隔
                      <InputNumber size="small" min={50} max={10000} step={100} disabled={readonly}
                        value={step.retryIntervalMs || 300} style={{ width: 90, margin: '0 4px' }}
                        onChange={v => onSaveStep(step.id, { retryIntervalMs: v || 300 })} /> ms 重发一次</span>
                  </Space>
                  {step.retryTimeoutMs > 0 && !['GET', 'HEAD', 'OPTIONS'].includes((step.method || 'GET').toUpperCase()) && (
                    <div style={{ marginTop: 10, color: '#e8453c' }}>
                      ⚠ {step.method} 上开了重试 —— 重试会<b>重发请求</b>，写操作重发会造出多份数据。
                      确认这个接口幂等再用，否则该把重试放到后面那个「读回来确认」的步骤上。
                    </div>
                  )}
                </div>
              ),
            },
            {
              key: 'response',
              label: (
                <span>
                  响应
                  {step._runResponse && !step._runResponse.error && (
                    <Tag
                      color={step._runResponse.statusCode < 400 ? 'cyan' : 'error'}
                      style={{ fontSize: 10, padding: '0 4px', lineHeight: '16px', marginLeft: 4 }}
                    >
                      {step._runResponse.statusCode}
                    </Tag>
                  )}
                  {step._runResponse?.error && <span style={{ color: '#e8453c', marginLeft: 4 }}>●</span>}
                </span>
              ),
              children: (
                <div style={{ background: 'transparent', borderRadius: 8, border: '1px solid rgba(0,0,0,0.06)', overflow: 'hidden' }}>
                  {step._runResponse ? (
                    step._runResponse.error ? (
                      <div style={{ padding: 16, color: '#e8453c' }}>{step._runResponse.error}</div>
                    ) : (
                      <>
                        <div style={{ padding: '8px 12px', background: 'rgba(255,255,255,0.3)', borderBottom: '1px solid rgba(0,0,0,0.04)', display: 'flex', gap: 12, fontSize: 12 }}>
                          <Tag color={step._runResponse.statusCode < 400 ? 'cyan' : 'error'}>{step._runResponse.statusCode}</Tag>
                          <span style={{ color: '#8c8c8c' }}>{step._runResponse.duration}ms</span>
                        </div>
                        <pre style={{ margin: 0, padding: 16, fontSize: 12, fontFamily: 'var(--font-mono)', lineHeight: 1.5, overflow: 'auto', maxHeight: 400 }}>
                          {JSON.stringify(step._runResponse.body, null, 2)}
                        </pre>
                      </>
                    )
                  ) : (
                    <div style={{ padding: 24, textAlign: 'center', color: '#bfbfbf', fontSize: 12 }}>
                      点击「运行」查看响应
                    </div>
                  )}
                </div>
              ),
            },
          ]}
        />
      </div>
    </>
  )
}
