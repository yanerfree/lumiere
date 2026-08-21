import { useState, useEffect, useRef, forwardRef, useImperativeHandle } from 'react'
import { Button, Select, Space, Tag, Tooltip, Upload, message, Modal, Input, Spin, Empty } from 'antd'
import {
  SaveOutlined, CodeOutlined, UploadOutlined,
  PlayCircleOutlined, CopyOutlined,
} from '@ant-design/icons'
import Editor from '@monaco-editor/react'
import { copyToClipboard } from '../utils/clipboard'
import { getValidToken } from '../utils/request'

const langMap = { python: 'python', typescript: 'typescript' }

const ScriptEditor = forwardRef(function ScriptEditor({
  projectId, branchId, caseId, scriptType,
  accentColor = '#0ea5a0',
  autoGenerateCode = null,
  onScriptSaved = null,
  envId = null,
  minimal = false,
  hideRun = false,
  hideVersions = false,
  hideToolbar = false,
}, ref) {
  const [script, setScript] = useState(null)
  const [versions, setVersions] = useState([])
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [showPaste, setShowPaste] = useState(false)
  const [pasteContent, setPasteContent] = useState('')
  const [running, setRunning] = useState(false)
  const [runResult, setRunResult] = useState(null)
  const editorRef = useRef(null)

  const apiBase = `/api/projects/${projectId}/branches/${branchId}/cases/${caseId}/scripts`
  // 每次请求前按需取（必要时刷新）access token，避免长会话拿到过期令牌
  const authHeaders = async () => ({ Authorization: `Bearer ${await getValidToken()}`, 'Content-Type': 'application/json' })

  const fetchActive = async () => {
    setLoading(true)
    try {
      const res = await fetch(`${apiBase}/active?type=${scriptType}`, { headers: await authHeaders() })
      const data = await res.json()
      if (data.data) {
        setScript(data.data)
        setContent(data.data.content || '')
        setDirty(false)
      } else {
        setScript(null)
        setContent('')
      }
    } catch { /* */ }
    finally { setLoading(false) }
  }

  const fetchVersions = async () => {
    try {
      const res = await fetch(`${apiBase}?type=${scriptType}`, { headers: await authHeaders() })
      const data = await res.json()
      setVersions(data.data || [])
    } catch { /* */ }
  }

  useEffect(() => {
    if (caseId && scriptType) { fetchActive(); fetchVersions() }
  }, [caseId, scriptType])

  const handleSave = async (newContent) => {
    const body = newContent || content
    if (!body.trim()) { message.warning('脚本内容不能为空'); return }
    setSaving(true)
    try {
      const res = await fetch(apiBase, {
        method: 'POST', headers: await authHeaders(),
        body: JSON.stringify({
          scriptType,
          content: body,
          fileName: script?.fileName || `test_${scriptType}.py`,
          funcName: script?.funcName || null,
          language: script?.language || 'python',
          source: 'manual',
        }),
      })
      const data = await res.json()
      if (data.data) {
        setScript(data.data)
        setContent(data.data.content)
        setDirty(false)
        message.success(`已保存 v${data.data.version}`)
        fetchVersions()
        onScriptSaved?.()
      }
    } catch { message.error('保存失败') }
    finally { setSaving(false) }
  }

  useImperativeHandle(ref, () => ({
    save: () => handleSave(),
    copyCode: () => { copyToClipboard(content); message.success('已复制到剪贴板') },
    refresh: () => { fetchActive(); fetchVersions() },
  }))

  const handleActivateVersion = async (scriptId) => {
    try {
      const res = await fetch(`${apiBase}/${scriptId}/activate`, { method: 'POST', headers: await authHeaders() })
      const data = await res.json()
      if (data.data) {
        setScript(data.data)
        setContent(data.data.content)
        setDirty(false)
        message.success(`已切换到 v${data.data.version}`)
        fetchVersions()
      }
    } catch { message.error('版本切换失败') }
  }

  const handleRun = async () => {
    if (dirty) { message.warning('请先保存脚本'); return }
    if (!script?.id) { message.warning('请先保存脚本'); return }
    setRunning(true)
    setRunResult(null)
    try {
      const res = await fetch(`${apiBase}/run?type=${scriptType}`, {
        method: 'POST',
        headers: await authHeaders(),
        body: JSON.stringify({ envId: envId || null }),
      })
      const data = await res.json()
      if (data.data) {
        setRunResult(data.data)
        message.info(data.data.status === 'passed' ? '执行通过 ✓' : `执行结果: ${data.data.status}`)
      } else {
        message.error(data?.error?.message || '执行失败')
      }
    } catch { message.error('执行请求失败') }
    finally { setRunning(false) }
  }

  const handleCreate = (initialContent = '') => {
    const tpl = initialContent || (scriptType === 'api'
      ? `import pytest\nimport httpx\n\n\nBASE_URL = "http://localhost:8000"\n\n\ndef test_example():\n    \"\"\"示例测试\"\"\"\n    response = httpx.get(f"{BASE_URL}/api/health")\n    assert response.status_code == 200\n`
      : `from playwright.sync_api import Page\n\n\ndef test_example(page: Page):\n    \"\"\"示例 UI 测试\"\"\"\n    page.goto("http://localhost:3000")\n    assert page.title()\n`)
    setContent(tpl)
    setScript({ language: scriptType === 'ui' ? 'typescript' : 'python', fileName: `test_${scriptType}.py` })
    setDirty(true)
  }

  const handleUpload = (file) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      const text = e.target.result
      setContent(text)
      setScript(prev => prev ? { ...prev } : { language: file.name.endsWith('.ts') ? 'typescript' : 'python', fileName: file.name })
      setDirty(true)
      message.success(`已加载 ${file.name}`)
    }
    reader.readAsText(file)
    return false
  }

  if (loading) return <div style={{ textAlign: 'center', padding: 32 }}><Spin tip="加载脚本..." /></div>

  if (!script && !dirty) return (
    minimal ? (
      <div style={{ padding: 24, textAlign: 'center', color: '#c9cdd4', fontSize: 13 }}>
        点击「AI 重新生成」生成 Playwright 脚本
      </div>
    ) : (
    <div style={{ padding: '16px 0' }}>
      <Empty description={`暂无${scriptType === 'api' ? '接口' : 'UI'}测试脚本`} image={Empty.PRESENTED_IMAGE_SIMPLE}>
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 8 }}>
          <Space>
            {autoGenerateCode && (
              <Button type="primary" icon={<CodeOutlined />} onClick={() => handleCreate(autoGenerateCode)}>从步骤生成代码</Button>
            )}
            <Button icon={<CodeOutlined />} onClick={() => handleCreate()}>创建空白脚本</Button>
            <Upload accept=".py,.ts" showUploadList={false} beforeUpload={handleUpload}>
              <Button icon={<UploadOutlined />}>上传文件</Button>
            </Upload>
          </Space>
        </div>
      </Empty>
      <Modal title="粘贴脚本代码" open={showPaste} onCancel={() => setShowPaste(false)}
        onOk={() => { if (pasteContent.trim()) { handleCreate(pasteContent); setShowPaste(false); setPasteContent('') } }}>
        <Input.TextArea spellCheck={false} rows={12} value={pasteContent} onChange={e => setPasteContent(e.target.value)}
          placeholder="在此粘贴 Python / TypeScript 测试脚本..." style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }} />
      </Modal>
    </div>
    )
  )

  const language = langMap[script?.language] || 'python'

  if (minimal) {
    return (
      <div style={{ border: '1px solid rgba(0,0,0,0.04)', borderRadius: 8, overflow: 'hidden' }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 8,
          padding: '4px 12px', background: '#1e1e1e', borderBottom: '1px solid #333',
        }}>
          <Tag color={accentColor} style={{ fontSize: 11, margin: 0 }}>{language}</Tag>
          {script?.fileName && <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: '#aaa' }}>{script.fileName}</span>}
          {script?.version && <Tag style={{ fontSize: 11, margin: 0, background: '#333', color: '#aaa', border: 'none' }}>v{script.version}</Tag>}
        </div>
        <Editor
          height={400}
          language={language}
          theme="vs-dark"
          value={content}
          options={{
            minimap: { enabled: false },
            fontSize: 13,
            lineNumbers: 'on',
            scrollBeyondLastLine: false,
            automaticLayout: true,
            tabSize: 4,
            wordWrap: 'on',
            padding: { top: 8 },
            readOnly: true,
          }}
        />
      </div>
    )
  }

  return (
    <div style={{ border: '1px solid rgba(0,0,0,0.04)', borderRadius: 8, overflow: 'hidden' }}>
      {/* 工具栏 */}
      {!hideToolbar && (
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '6px 12px', background: '#1e1e1e', borderBottom: '1px solid #333',
      }}>
        <Space size={8}>
          <Tag color={accentColor} style={{ fontSize: 11, margin: 0 }}>{language}</Tag>
          {script?.fileName && <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: '#aaa' }}>{script.fileName}</span>}
          {dirty && <Tag color="orange" style={{ fontSize: 11, margin: 0 }}>未保存</Tag>}
          {script?.version && <Tag style={{ fontSize: 11, margin: 0, background: '#333', color: '#aaa', border: 'none' }}>v{script.version}</Tag>}
        </Space>
        <Space size={6}>
          {!hideVersions && versions.length > 1 && (
            <Select size="small" style={{ width: 100 }} value={script?.id}
              onChange={(v) => handleActivateVersion(v)}
              options={versions.map(ver => ({
                value: ver.id,
                label: <span style={{ fontSize: 11 }}>v{ver.version}{ver.status === 'active' ? ' ✓' : ''}</span>,
              }))} />
          )}
          <Tooltip title="保存 (Ctrl+S)">
            <Button size="small" type="primary" icon={<SaveOutlined />} loading={saving}
              disabled={!dirty} onClick={() => handleSave()}>保存</Button>
          </Tooltip>
          <Tooltip title="复制代码">
            <Button size="small" type="text" icon={<CopyOutlined />} style={{ color: '#aaa' }}
              onClick={() => { copyToClipboard(content); message.success('已复制') }} />
          </Tooltip>
          {!hideRun && (
          <Tooltip title={dirty ? '请先保存' : '运行脚本'}>
            <Button size="small" icon={<PlayCircleOutlined />} loading={running}
              disabled={dirty || !script?.id}
              style={{ color: '#0ea5a0', borderColor: '#0ea5a0' }}
              onClick={handleRun}>运行</Button>
          </Tooltip>
          )}
        </Space>
      </div>
      )}

      {/* Monaco 编辑器 */}
      <Editor
        height={400}
        language={language}
        theme="vs-dark"
        value={content}
        onChange={(val) => { setContent(val || ''); setDirty(true) }}
        onMount={(editor) => {
          editorRef.current = editor
          editor.addCommand(2097 /* KeyMod.CtrlCmd | KeyCode.KeyS */, () => handleSave())
        }}
        options={{
          minimap: { enabled: false },
          fontSize: 13,
          lineNumbers: 'on',
          scrollBeyondLastLine: false,
          automaticLayout: true,
          tabSize: 4,
          wordWrap: 'on',
          padding: { top: 8 },
        }}
      />

      {/* 执行结果面板（hideRun 时外部处理） */}
      {!hideRun && (running || runResult) && (
        <div style={{ borderTop: '1px solid #333', background: '#1a1a1a', padding: '12px 16px' }}>
          {running ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, color: '#aaa' }}>
              <Spin size="small" /> <span>正在执行...</span>
            </div>
          ) : runResult && (
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, marginBottom: 8 }}>
                <Tag color={runResult.status === 'passed' ? 'success' : runResult.status === 'failed' ? 'error' : 'warning'}
                  style={{ fontSize: 13, padding: '2px 12px' }}>
                  {runResult.status === 'passed' ? '✓ PASSED' : runResult.status === 'failed' ? '✗ FAILED' : runResult.status?.toUpperCase()}
                </Tag>
                <span style={{ fontSize: 12, color: '#86909c' }}>{runResult.durationMs}ms</span>
                <Button size="small" type="text" style={{ color: '#aaa', marginLeft: 'auto' }}
                  onClick={() => setRunResult(null)}>关闭</Button>
              </div>
              {runResult.errorSummary && (
                <div style={{ padding: '8px 12px', background: '#2d1215', borderRadius: 6, marginBottom: 8, fontSize: 12, color: '#ff7875', fontFamily: 'var(--font-mono)', whiteSpace: 'pre-wrap', maxHeight: 120, overflow: 'auto' }}>
                  {runResult.errorSummary}
                </div>
              )}
              {runResult.stdout && (
                <details style={{ fontSize: 12 }}>
                  <summary style={{ color: '#86909c', cursor: 'pointer', marginBottom: 4 }}>执行日志</summary>
                  <pre style={{ margin: 0, padding: '8px 12px', background: '#111', borderRadius: 6, color: '#d4d4d4', fontSize: 11, maxHeight: 250, overflow: 'auto', whiteSpace: 'pre-wrap' }}>
                    {runResult.stdout}
                  </pre>
                </details>
              )}
            </div>
          )}
        </div>
      )}

      {/* 粘贴弹窗 */}
      <Modal title="粘贴脚本代码" open={showPaste} onCancel={() => setShowPaste(false)}
        onOk={() => { if (pasteContent.trim()) { setContent(pasteContent); setDirty(true); setShowPaste(false); setPasteContent('') } }}>
        <Input.TextArea spellCheck={false} rows={12} value={pasteContent} onChange={e => setPasteContent(e.target.value)}
          placeholder="在此粘贴代码..." style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }} />
      </Modal>
    </div>
  )
})

export default ScriptEditor
