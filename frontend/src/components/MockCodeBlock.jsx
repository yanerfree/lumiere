import { Button } from 'antd'
import { CopyOutlined } from '@ant-design/icons'

const MONO = 'var(--font-mono)'

/**
 * Mock 模块统一的报文块配色 —— 浅色系，对齐全站色板
 * （--bg-hover / --text-primary，见 styles/global.css）。
 * 展示请求/响应报文用这一套；代码编辑器（ScriptEditor、k6 脚本）保持深色，不走这里。
 */
export const CODE_BLOCK_STYLE = {
  background: 'rgba(0,0,0,0.04)',
  color: '#1d2129',
  border: '1px solid rgba(0,0,0,0.06)',
  fontFamily: MONO,
}

/** 带标题和复制按钮的报文块（请求头/请求体/响应头/响应体） */
export function LogBlock({ title, content, onCopy, maxHeight = 280 }) {
  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 6 }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: '#4e5969' }}>{title}</span>
        <span style={{ flex: 1 }} />
        {onCopy && content && content !== '-' && (
          <Button size="small" type="text" icon={<CopyOutlined />}
            style={{ fontSize: 11, color: '#86909c' }} onClick={onCopy}>复制</Button>
        )}
      </div>
      <pre style={{
        ...CODE_BLOCK_STYLE,
        margin: 0, padding: 12, borderRadius: 12, maxHeight, overflow: 'auto',
        fontSize: 12, lineHeight: 1.6,
        whiteSpace: 'pre-wrap', wordBreak: 'break-all',
      }}>{content}</pre>
    </div>
  )
}
