import { useState } from 'react'
import { Button, Modal, Form, Input, TreeSelect, Select, Typography, message } from 'antd'
import { RobotOutlined, LoadingOutlined, CopyOutlined, CodeOutlined } from '@ant-design/icons'
import { copyToClipboard } from '../../../utils/clipboard'

const { TextArea } = Input
const { Text } = Typography

export default function GenerateModal({
  open, onClose,
  form, generating, genProgress,
  onGenerate,
  folderTree,
  environments, apiList,
  projectName, branchName, branchId,
}) {
  const [inputMode, setInputMode] = useState('manual')

  const buildParentSelect = (nodes) => nodes.map(n => ({
    value: n.id, title: n.name,
    children: n.children?.length > 0 ? buildParentSelect(n.children) : undefined,
  }))

  const mcpUrl = `http://${window.location.hostname}:18800/mcp/`
  const mcpConfig = JSON.stringify({
    mcpServers: {
      testbench: {
        url: mcpUrl,
        transport: "streamable-http",
        headers: { Authorization: "Bearer <你的API Key>" }
      }
    }
  }, null, 2)

  const copyText = (text) => {
    copyToClipboard(text).then(() => message.success('已复制'))
  }

  const modes = [
    { key: 'manual', label: '手动输入' },
    { key: 'apiList', label: `API 列表 (${apiList?.length || 0})` },
    { key: 'claude', label: 'Claude Code' },
  ]

  return (
    <Modal
      title="生成接口测试"
      open={open}
      onCancel={() => { if (!generating) { onClose(); setInputMode('manual') } }}
      width={640}
      footer={generating ? null : inputMode === 'claude' ? [
        <Button key="close" onClick={() => { onClose(); setInputMode('manual') }}>关闭</Button>,
      ] : [
        <Button key="cancel" onClick={() => { onClose(); setInputMode('manual') }}>取消</Button>,
        <Button key="gen" type="primary" icon={<RobotOutlined />} onClick={onGenerate}>开始生成</Button>,
      ]}
    >
      {!generating ? (
        <>
          {/* 模式切换 */}
          <div style={{ display: 'flex', gap: 0, marginBottom: 16 }}>
            {modes.map((t, i) => (
              <Button key={t.key} size="small"
                type={inputMode === t.key ? 'primary' : 'default'}
                icon={t.key === 'claude' ? <CodeOutlined /> : undefined}
                onClick={() => setInputMode(t.key)}
                style={{
                  borderRadius: 0,
                  ...(i === 0 ? { borderRadius: '6px 0 0 6px' } : {}),
                  ...(i === modes.length - 1 ? { borderRadius: '0 6px 6px 0' } : {}),
                }}>
                {t.label}
              </Button>
            ))}
          </div>

          {inputMode === 'claude' ? (
            /* ── Claude Code 方式 ── */
            <div style={{ fontSize: 13, lineHeight: 1.8 }}>
              <div style={{ padding: '10px 14px', background: 'rgba(14,165,160,0.06)', borderRadius: 8, marginBottom: 16, border: '1px solid rgba(14,165,160,0.15)' }}>
                <div style={{ fontWeight: 600, marginBottom: 4, color: '#0ea5a0' }}>
                  <CodeOutlined /> 通过 Claude Code 生成
                </div>
                <div style={{ color: '#595959', fontSize: 12 }}>
                  在终端的 Claude Code 中输入自然语言指令，AI 会分析你的项目代码并自动生成测试场景到本页面。
                </div>
              </div>

              <div style={{ marginBottom: 12 }}>
                <div style={{ fontWeight: 600, marginBottom: 6 }}>1. 配置 MCP 连接</div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 4 }}>
                  将以下内容保存到项目根目录 <Text code>.mcp.json</Text>（API Key 在 AI 智能 → MCP 工具 页面创建）
                </div>
                <div style={{ position: 'relative' }}>
                  <pre style={{
                    background: 'rgba(0,0,0,0.02)', border: '1px solid rgba(0,0,0,0.06)', borderRadius: 6,
                    padding: '10px 14px', fontSize: 11, fontFamily: "'SF Mono', Monaco, Consolas, monospace",
                    overflow: 'auto', maxHeight: 140, margin: 0,
                  }}>{mcpConfig}</pre>
                  <Button size="small" icon={<CopyOutlined />}
                    style={{ position: 'absolute', top: 6, right: 6, fontSize: 11 }}
                    onClick={() => copyText(mcpConfig)}>复制</Button>
                </div>
              </div>

              <div>
                <div style={{ fontWeight: 600, marginBottom: 6 }}>2. 在 Claude Code 中输入</div>
                <div style={{ fontSize: 12, color: '#86909c', marginBottom: 6 }}>
                  当前项目：<Text strong>{projectName || '-'}</Text>，分支：<Text strong>{branchName || '-'}</Text>
                  {branchId && <span style={{ marginLeft: 8 }}>(branch_id: <Text code copyable={{ text: branchId }} style={{ fontSize: 11 }}>{branchId.substring(0, 8)}...</Text>)</span>}
                </div>
                {[
                  `在测试平台「${projectName || '当前项目'}」的「${branchName || 'default'}」分支下，为用户管理 API 生成接口测试`,
                  `在测试平台「${projectName || '当前项目'}」的「${branchName || 'default'}」分支下，为 POST /api/users 生成接口测试，覆盖正向、参数校验、权限测试`,
                  `在测试平台「${projectName || '当前项目'}」的「${branchName || 'default'}」分支下，分析项目所有 API 接口，批量生成接口测试场景`,
                ].map((cmd, i) => (
                  <div key={i} style={{
                    display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                    padding: '6px 12px', background: 'rgba(0,0,0,0.02)', borderRadius: 6, marginBottom: 4,
                    fontFamily: 'monospace', fontSize: 12,
                  }}>
                    <span style={{ flex: 1 }}>{cmd}</span>
                    <Button type="text" size="small" icon={<CopyOutlined />}
                      onClick={() => copyText(cmd)} style={{ flexShrink: 0, fontSize: 11 }} />
                  </div>
                ))}
                <div style={{ fontSize: 11, color: '#86909c', marginTop: 6 }}>
                  生成完成后刷新本页面即可看到新场景。
                </div>
              </div>
            </div>
          ) : (
            /* ── 平台内生成方式 ── */
            <Form form={form} layout="vertical">
              {inputMode === 'apiList' && apiList?.length > 0 ? (
                <Form.Item name="apiIds" label="选择接口" rules={[{ required: true, message: '请选择至少一个接口' }]}>
                  <Select mode="multiple" placeholder="选择要生成测试的接口" allowClear
                    options={apiList.map(a => ({
                      value: a.id,
                      label: `${a.method || 'GET'} ${a.url || ''} — ${a.name || ''}`.trim(),
                    }))}
                    maxTagCount={5} showSearch optionFilterProp="label"
                    style={{ width: '100%' }} />
                </Form.Item>
              ) : inputMode === 'apiList' ? (
                <div style={{ padding: '16px', textAlign: 'center', color: '#86909c', fontSize: 13, background: 'rgba(0,0,0,0.02)', borderRadius: 8, marginBottom: 16 }}>
                  当前分支暂无 API 接口数据。请先到 <Text strong>API 接口</Text> 页面录入接口，或切换到"手动输入"模式。
                </div>
              ) : (
                <Form.Item name="apiInfo" label="接口定义" rules={[{ required: true, message: '请输入接口定义' }]}>
                  <TextArea rows={8} placeholder={"粘贴接口定义，例如：\n\n### POST /api/users — 创建用户\n参数:\n- username (string, required, 3-100位)\n- password (string, required, ≥6位)\n- role (string, required, enum: admin/user)\n需要认证：Bearer Token"} />
                </Form.Item>
              )}

              <Form.Item name="targetFolder" label="目标文件夹（可选）">
                <TreeSelect
                  treeData={buildParentSelect(folderTree)}
                  placeholder="不选则 AI 自动创建"
                  allowClear
                  style={{ width: '100%' }}
                />
              </Form.Item>
              <Form.Item name="envId" label="运行环境（可选）">
                <Select placeholder="选择环境（变量将注入测试步骤）" allowClear
                  options={(environments || []).map(e => ({ value: e.id, label: e.name }))} />
              </Form.Item>
              <Form.Item name="envVars" label="额外环境变量 (JSON，可选)">
                <TextArea rows={2} placeholder={'{"CUSTOM_VAR": "value"}'} />
              </Form.Item>
            </Form>
          )}
        </>
      ) : (
        <div>
          <div style={{ textAlign: 'center', marginBottom: 16 }}>
            <LoadingOutlined style={{ fontSize: 24 }} />
            <div style={{ marginTop: 8, fontWeight: 500 }}>正在生成...</div>
          </div>
          <div style={{ padding: '8px 12px', background: 'rgba(0,0,0,0.03)', borderRadius: 8, maxHeight: 200, overflow: 'auto' }}>
            {genProgress.map((p, i) => <div key={i} style={{ fontSize: 12, color: '#595959', padding: '2px 0' }}>{p}</div>)}
          </div>
        </div>
      )}
    </Modal>
  )
}
