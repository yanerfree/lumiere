import { Button, Input, InputNumber, Select, Checkbox, Tooltip, Empty } from 'antd'
import { PlusOutlined, DeleteOutlined } from '@ant-design/icons'

const MONO = 'var(--font-mono)'

const TYPES = ['string', 'integer', 'number', 'boolean', 'array', 'object']

const cell = { padding: '4px 6px', verticalAlign: 'top' }
const head = {
  padding: '4px 6px', fontSize: 11, color: '#86909c', fontWeight: 500,
  textAlign: 'left', borderBottom: '1px solid rgba(0,0,0,0.06)', whiteSpace: 'nowrap',
}

/**
 * 工具入参表编辑器。
 *
 * ⚠ 参数是**有序列表**，不是「名字 → 类型」的对象：页面上的顺序会原样进
 *   tools/list 的 schema，对面客户端按这个顺序给人填参数。用对象存的话
 *   顺序靠插入序撑着，存一趟数据库回来就乱了，而且乱得没有报错。
 */
export default function ParamEditor({ value, onChange, disabled }) {
  const params = Array.isArray(value) ? value : []

  const patch = (i, kv) => {
    const next = params.map((p, idx) => (idx === i ? { ...p, ...kv } : p))
    onChange(next)
  }
  const add = () => onChange([...params, { name: '', type: 'string', required: false, description: '' }])
  const del = (i) => onChange(params.filter((_, idx) => idx !== i))

  // 范围那两列对不同类型是不同的东西：数字是大小，字符串是长度，其它没有
  const rangeKeys = (t) => {
    if (t === 'integer' || t === 'number') return ['minimum', 'maximum']
    if (t === 'string') return ['minLength', 'maxLength']
    return null
  }

  return (
    <div>
      {params.length === 0 ? (
        <div style={{ padding: '12px 0' }}>
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE}
            description={<span style={{ color: '#c9cdd4', fontSize: 12 }}>这个工具不需要参数</span>} />
        </div>
      ) : (
        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
          <thead>
            <tr>
              <th style={{ ...head, width: 130 }}>参数名</th>
              <th style={{ ...head, width: 92 }}>类型</th>
              <th style={{ ...head, width: 48, textAlign: 'center' }}>必填</th>
              <th style={head}>说明</th>
              <th style={{ ...head, width: 100 }}>默认值</th>
              <th style={{ ...head, width: 130 }}>只能填这几个<br /><span style={{ fontWeight: 400 }}>（逗号分隔）</span></th>
              <th style={{ ...head, width: 130 }}>范围</th>
              <th style={{ ...head, width: 32 }} />
            </tr>
          </thead>
          <tbody>
            {params.map((p, i) => {
              const rk = rangeKeys(p.type)
              return (
                <tr key={i} style={{ borderBottom: '1px solid rgba(0,0,0,0.03)' }}>
                  <td style={cell}>
                    <Input size="small" spellCheck={false} disabled={disabled}
                      style={{ fontFamily: MONO, fontSize: 11 }}
                      value={p.name} placeholder="order_no"
                      onChange={e => patch(i, { name: e.target.value })} />
                  </td>
                  <td style={cell}>
                    <Select size="small" style={{ width: '100%' }} disabled={disabled}
                      value={p.type || 'string'} onChange={v => patch(i, { type: v })}
                      options={TYPES.map(t => ({ value: t, label: t }))} />
                  </td>
                  <td style={{ ...cell, textAlign: 'center', paddingTop: 8 }}>
                    <Checkbox checked={!!p.required} disabled={disabled}
                      onChange={e => patch(i, { required: e.target.checked })} />
                  </td>
                  <td style={cell}>
                    <Input size="small" disabled={disabled} value={p.description || ''}
                      placeholder="给对面客户端看的说明"
                      onChange={e => patch(i, { description: e.target.value })} />
                  </td>
                  <td style={cell}>
                    <Input size="small" spellCheck={false} disabled={disabled}
                      style={{ fontFamily: MONO, fontSize: 11 }}
                      value={p.default === undefined || p.default === null ? '' : String(p.default)}
                      onChange={e => {
                        const raw = e.target.value
                        patch(i, { default: raw === '' ? undefined : coerce(raw, p.type) })
                      }} />
                  </td>
                  <td style={cell}>
                    <Input size="small" spellCheck={false} disabled={disabled}
                      style={{ fontSize: 11 }} placeholder="P0,P1,P2"
                      value={Array.isArray(p.enum) ? p.enum.join(',') : (p.enum || '')}
                      onChange={e => {
                        const raw = e.target.value
                        const list = raw.split(',').map(s => s.trim()).filter(Boolean)
                        patch(i, { enum: list.length ? list : undefined })
                      }} />
                  </td>
                  <td style={cell}>
                    {rk ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
                        <InputNumber size="small" disabled={disabled} style={{ width: 58 }}
                          placeholder={rk[0] === 'minimum' ? '最小' : '最短'}
                          value={p[rk[0]] ?? null}
                          onChange={v => patch(i, { [rk[0]]: v ?? undefined })} />
                        <span style={{ color: '#c9cdd4' }}>~</span>
                        <InputNumber size="small" disabled={disabled} style={{ width: 58 }}
                          placeholder={rk[1] === 'maximum' ? '最大' : '最长'}
                          value={p[rk[1]] ?? null}
                          onChange={v => patch(i, { [rk[1]]: v ?? undefined })} />
                      </div>
                    ) : p.type === 'array' ? (
                      <Select size="small" style={{ width: '100%' }} disabled={disabled}
                        placeholder="元素类型" allowClear
                        value={p.itemsType || undefined}
                        onChange={v => patch(i, { itemsType: v || undefined })}
                        options={['string', 'integer', 'number', 'boolean', 'object'].map(t => ({ value: t, label: t }))} />
                    ) : (
                      <span style={{ fontSize: 11, color: '#c9cdd4' }}>—</span>
                    )}
                  </td>
                  <td style={{ ...cell, textAlign: 'center', paddingTop: 6 }}>
                    <Tooltip title="删掉这个参数">
                      <Button size="small" type="text" danger disabled={disabled}
                        icon={<DeleteOutlined />} onClick={() => del(i)} />
                    </Tooltip>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      )}
      <Button size="small" type="dashed" icon={<PlusOutlined />} disabled={disabled}
        style={{ marginTop: 8 }} onClick={add}>加一个参数</Button>
    </div>
  )
}

/** 默认值按类型收：页面上填的都是字符串，存成 "1" 而不是 1 的话，schema 里的默认值类型就和声明的类型对不上。 */
function coerce(raw, type) {
  if (type === 'integer') { const n = parseInt(raw, 10); return Number.isNaN(n) ? raw : n }
  if (type === 'number') { const n = Number(raw); return Number.isNaN(n) ? raw : n }
  if (type === 'boolean') return raw === 'true' || raw === '1'
  if (type === 'array' || type === 'object') {
    try { return JSON.parse(raw) } catch { return raw }
  }
  return raw
}
