import { useMemo } from 'react'
import { Button, Input, Radio, Switch, Space, Tag, Tooltip, Popconfirm, Alert, message } from 'antd'
import { SaveOutlined, CopyOutlined, DeleteOutlined, LockOutlined, LockFilled, UnlockOutlined, ApiOutlined } from '@ant-design/icons'
import { copyToClipboard } from '../../utils/clipboard'

const MONO = 'var(--font-mono)'
const { TextArea } = Input

const label = { fontSize: 12, color: '#86909c', marginBottom: 4 }
const hint = { fontSize: 11, color: '#c9cdd4', marginTop: 4, lineHeight: 1.6 }

/**
 * 一个 Mock 服务的设置页。
 *
 * ⚠ 下拉/单选的选项一律从 `GET /api/mcp-mock/meta` 拿，别在这儿再抄一份：
 *   抄一份就意味着后端加了传输方式、页面上不会有，而且**没有任何报错**。
 */
export default function ServerSettings({
  meta, form, setForm, dirty, saving, running,
  onSave, onToggleLock, onToggleEnabled, onDelete,
}) {
  const locked = !!form.locked
  const builtin = !!form.builtin
  const ro = locked

  const transports = meta.transports || []
  const authTypes = meta.authTypes || []
  const validateModes = meta.validateModes || []

  const curValidateHint = useMemo(
    () => (validateModes.find(v => v.value === form.validateMode) || {}).hint,
    [validateModes, form.validateMode],
  )
  const curTransportHint = useMemo(
    () => (transports.find(v => v.value === form.transport) || {}).hint,
    [transports, form.transport],
  )

  const cfg = form.authConfig || {}
  const setCfg = (kv) => setForm(f => ({ ...f, authConfig: { ...(f.authConfig || {}), ...kv } }))

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* 头部：名字 + 保存/锁/删 */}
      <div style={{
        padding: '10px 16px', borderBottom: '1px solid rgba(0,0,0,0.04)', flexShrink: 0,
        display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
          {builtin && <Tag color="purple" style={{ margin: 0, fontSize: 11 }}>预置</Tag>}
          {locked && <Tag color="orange" icon={<LockFilled />} style={{ margin: 0, fontSize: 11 }}>已锁定</Tag>}
          <span style={{ fontWeight: 600, fontSize: 14, color: '#1d2129' }}>{form.name}</span>
          <Tag style={{ margin: 0, fontSize: 11, fontFamily: MONO, borderRadius: 8 }}>/{form.slug}</Tag>
        </div>
        <Space size={8}>
          <Button type="primary" size="small" icon={<SaveOutlined />}
            onClick={onSave} loading={saving} disabled={!dirty || ro}>保存</Button>
          <Switch size="small" checked={!!form.enabled} disabled={ro}
            onChange={onToggleEnabled} checkedChildren="启用" unCheckedChildren="停用" />
          <Tooltip title={locked ? '解锁后可编辑' : '锁上之后不能改也不能删'}>
            <Button size="small" icon={locked ? <UnlockOutlined /> : <LockOutlined />}
              type={locked ? 'primary' : 'default'} ghost={locked} onClick={onToggleLock}>
              {locked ? '解锁' : '锁定'}
            </Button>
          </Tooltip>
          {builtin ? (
            <Tooltip title="预置服务不能删 —— 它是随平台一起装好的示例，删了下次启动还会回来。不想用就用上面的开关停掉。">
              <Button size="small" danger icon={<DeleteOutlined />} disabled />
            </Tooltip>
          ) : locked ? (
            <Tooltip title="已锁定，请先解锁"><Button size="small" danger icon={<DeleteOutlined />} disabled /></Tooltip>
          ) : (
            <Popconfirm title="确认删除这个服务？" description="它下面的工具和调用日志会一起删掉。"
              okText="删除" cancelText="再想想" onConfirm={onDelete}>
              <Button size="small" danger icon={<DeleteOutlined />} />
            </Popconfirm>
          )}
        </Space>
      </div>

      <div style={{ flex: 1, overflow: 'auto', padding: '14px 16px' }}>
        {locked && (
          <Alert type="warning" showIcon style={{ marginBottom: 12, fontSize: 12 }}
            message="这个服务已锁定，下面都是只读的。点右上角「解锁」才能改。" />
        )}
        {builtin && !locked && (
          <Alert type="info" showIcon style={{ marginBottom: 12, fontSize: 12 }}
            message="这是预置服务，可以改、可以停用，但删不掉（下次启动平台会把它补回来）。" />
        )}

        {/* 接入地址 */}
        <div style={{ marginBottom: 16 }}>
          <div style={label}>接入地址（填到客户端里的就是这一条）</div>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '6px 12px', borderRadius: 12,
            background: running && form.enabled ? 'var(--green-bg)' : 'rgba(0,0,0,0.02)',
            border: `1px solid ${running && form.enabled ? 'rgba(14,165,160,0.3)' : 'rgba(0,0,0,0.06)'}`,
          }}>
            <ApiOutlined style={{ fontSize: 12, color: running && form.enabled ? '#0ea5a0' : '#c9cdd4' }} />
            <span style={{
              flex: 1, fontSize: 12, fontFamily: MONO, userSelect: 'all',
              color: running && form.enabled ? '#0ea5a0' : '#86909c',
            }}>{form.url}</span>
            <Button size="small" type="text" icon={<CopyOutlined />}
              style={{ color: running && form.enabled ? '#0ea5a0' : '#86909c' }}
              onClick={() => copyToClipboard(form.url).then(() => message.success('已复制接入地址'))} />
          </div>
          {!running && <div style={hint}>服务总开关没开，这个地址现在连不上 —— 点右上角「启动」。</div>}
          {running && !form.enabled && <div style={hint}>这个服务被停用了，地址会返回 404。打开上面的开关就能用。</div>}
        </div>

        {/* 名称 / 代号 */}
        <div style={{ display: 'flex', gap: 12, marginBottom: 16 }}>
          <div style={{ flex: 1 }}>
            <div style={label}>服务名称</div>
            <Input size="small" value={form.name} disabled={ro}
              onChange={e => setForm(f => ({ ...f, name: e.target.value }))} />
          </div>
          <div style={{ width: 200 }}>
            <div style={label}>服务代号</div>
            <Input size="small" value={form.slug} disabled readOnly style={{ fontFamily: MONO }} />
            <div style={hint}>建好就不能改 —— 它在地址里，改了对面客户端就连不上了。</div>
          </div>
        </div>

        {/* 说明 */}
        <div style={{ marginBottom: 16 }}>
          <div style={label}>说明（只给自己看）</div>
          <Input size="small" value={form.description || ''} disabled={ro}
            placeholder="这个服务用来测什么"
            onChange={e => setForm(f => ({ ...f, description: e.target.value }))} />
        </div>

        {/* instructions */}
        <div style={{ marginBottom: 16 }}>
          <div style={label}>给对面客户端的说明</div>
          <TextArea rows={2} size="small" value={form.instructions || ''} disabled={ro}
            placeholder="连上之后客户端会读到这段话，一般写「这个服务是干嘛的」"
            onChange={e => setForm(f => ({ ...f, instructions: e.target.value }))} />
        </div>

        {/* 传输方式 */}
        <div style={{ marginBottom: 16 }}>
          <div style={label}>传输方式</div>
          <Radio.Group size="small" buttonStyle="solid" value={form.transport} disabled={ro}
            onChange={e => setForm(f => ({ ...f, transport: e.target.value }))}>
            {transports.map(t => (
              <Tooltip key={t.value} title={t.hint}>
                <Radio.Button value={t.value} disabled={ro || !t.supported} style={{ fontSize: 11 }}>
                  {t.label}
                </Radio.Button>
              </Tooltip>
            ))}
          </Radio.Group>
          {curTransportHint && <div style={hint}>{curTransportHint}</div>}
        </div>

        {/* 认证 */}
        <div style={{ marginBottom: 16 }}>
          <div style={label}>认证方式</div>
          <Radio.Group size="small" buttonStyle="solid" value={form.authType} disabled={ro}
            onChange={e => setForm(f => ({ ...f, authType: e.target.value }))}>
            {authTypes.map(a => (
              <Radio.Button key={a.value} value={a.value} style={{ fontSize: 11 }}>{a.label}</Radio.Button>
            ))}
          </Radio.Group>

          {form.authType === 'bearer' && (
            <div style={{ marginTop: 8 }}>
              <div style={label}>Token</div>
              <Input size="small" spellCheck={false} style={{ fontFamily: MONO }} disabled={ro}
                value={cfg.token || ''} placeholder="lumiere-mock-token-2026"
                onChange={e => setCfg({ token: e.target.value })} />
              <div style={hint}>对面要在请求头里带 <code>Authorization: Bearer {cfg.token || '<token>'}</code>，不带或带错一律 401。</div>
            </div>
          )}

          {form.authType === 'apikey' && (
            <div style={{ marginTop: 8, display: 'flex', gap: 12 }}>
              <div style={{ width: 200 }}>
                <div style={label}>请求头名字</div>
                <Input size="small" spellCheck={false} style={{ fontFamily: MONO }} disabled={ro}
                  value={cfg.headerName || ''} placeholder="X-API-Key"
                  onChange={e => setCfg({ headerName: e.target.value })} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={label}>Key</div>
                <Input size="small" spellCheck={false} style={{ fontFamily: MONO }} disabled={ro}
                  value={cfg.apiKey || ''} placeholder="lumiere-mock-apikey-2026"
                  onChange={e => setCfg({ apiKey: e.target.value })} />
                <div style={hint}>对面要在请求头里带 <code>{cfg.headerName || 'X-API-Key'}: {cfg.apiKey || '<key>'}</code>。</div>
              </div>
            </div>
          )}
        </div>

        {/* 参数校验松紧 */}
        <div style={{ marginBottom: 16 }}>
          <div style={label}>参数校验松紧</div>
          <Radio.Group size="small" buttonStyle="solid" value={form.validateMode} disabled={ro}
            onChange={e => setForm(f => ({ ...f, validateMode: e.target.value }))}>
            {validateModes.map(v => (
              <Radio.Button key={v.value} value={v.value} style={{ fontSize: 11 }}>{v.label}</Radio.Button>
            ))}
          </Radio.Group>
          {curValidateHint && <div style={hint}>{curValidateHint}</div>}
        </div>

        {/* 统计 */}
        <div style={{ fontSize: 11, color: '#c9cdd4' }}>
          累计被调用 {form.callCount || 0} 次
          {form.lastCallAt ? `，最近一次 ${new Date(form.lastCallAt).toLocaleString('zh-CN', { hour12: false })}` : ''}
        </div>
      </div>
    </div>
  )
}
