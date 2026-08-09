import { useState } from 'react'
import { Input, Button, Upload, Collapse, Select, Space, Typography, message, Alert } from 'antd'
import { UploadOutlined, SendOutlined, FileMarkdownOutlined } from '@ant-design/icons'
import { api } from '../../../utils/request'

const { TextArea } = Input
const { Text } = Typography

export default function Stage1Input({ projectId, branchId, onTaskCreated }) {
  const [content, setContent] = useState('')
  const [title, setTitle] = useState('')
  const [source, setSource] = useState('paste')
  const [filename, setFilename] = useState(null)
  const [loading, setLoading] = useState(false)
  const [settings, setSettings] = useState({})

  const charCount = content.length
  const tokenEstimate = Math.ceil(charCount * 1.5)

  const handleUpload = (file) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      setContent(e.target.result)
      setTitle(file.name.replace(/\.md$/, ''))
      setSource('upload')
      setFilename(file.name)
    }
    reader.readAsText(file)
    return false
  }

  const handleSubmit = async () => {
    if (!content.trim()) { message.warning('请输入或上传需求文档'); return }
    if (!title.trim()) { message.warning('请输入任务名称'); return }
    if (!branchId) { message.warning('请先在顶部选择分支'); return }

    setLoading(true)
    try {
      const res = await api.post(
        `/projects/${projectId}/branches/${branchId}/scenario-gen/tasks`,
        { title: title.trim(), contentMarkdown: content, source, filename, settings },
      )
      message.success('任务已创建')
      onTaskCreated?.(res.data)
    } catch { /* request.js handles */ }
    finally { setLoading(false) }
  }

  return (
    <div style={{ maxWidth: 800, margin: '0 auto' }}>
      <div style={{ marginBottom: 20, padding: '12px 16px', background: 'rgba(124,172,248,0.06)', borderRadius: 8, border: '1px solid rgba(124,172,248,0.15)' }}>
        <Text style={{ fontSize: 13, color: '#4e6a8a' }}>
          <strong>这是什么：</strong>从需求文档自动生成<strong>手工测试用例</strong>（操作步骤 + 预期结果），
          就是平时手写的"1. 打开登录页 → 2. 输入用户名密码 → 3. 点击登录 → 预期：跳转首页"这种。
          <br/>
          <strong>怎么用：</strong>把 PRD、用户故事或功能描述粘贴到下方，AI 会提取需求点、生成场景模型，然后批量展开为测试用例。
          <br/>
          <Text type="secondary" style={{ fontSize: 12 }}>
            生成的用例会进入「用例管理」的待审核队列，审核通过后可加入测试计划执行。
          </Text>
        </Text>
      </div>

      <div style={{ marginBottom: 16 }}>
        <Text strong>任务名称</Text>
        <Input
          placeholder="给这次生成取个名字，如：订单退款需求 v2、会员等级规则"
          value={title}
          onChange={e => setTitle(e.target.value)}
          style={{ marginTop: 4 }}
          maxLength={200}
        />
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 4 }}>
          <Text strong>需求材料 *</Text>
          <Upload accept=".md" showUploadList={false} beforeUpload={handleUpload}>
            <Button size="small" icon={<UploadOutlined />}>上传 .md</Button>
          </Upload>
        </div>
        <TextArea spellCheck={false}
          placeholder="粘贴需求文档内容（支持 Markdown 和纯文本）&#10;&#10;示例：&#10;# 用户登录功能&#10;&#10;## 正常登录&#10;用户输入用户名和密码，点击登录按钮，系统验证成功后跳转到首页。&#10;&#10;## 密码错误&#10;密码错误时显示「用户名或密码错误」提示。"
          value={content}
          onChange={e => { setContent(e.target.value); if (source === 'upload') setSource('paste') }}
          rows={12}
          style={{ fontFamily: 'var(--font-mono)', fontSize: 13 }}
        />
        {filename && source === 'upload' && (
          <div style={{ marginTop: 4 }}>
            <FileMarkdownOutlined style={{ color: '#7cacf8', marginRight: 4 }} />
            <Text type="secondary">{filename}</Text>
          </div>
        )}
      </div>

      <Collapse
        ghost
        size="small"
        items={[{
          key: 'context',
          label: <Text type="secondary">增强上下文（可选，提升生成质量）</Text>,
          children: (
            <Space direction="vertical" style={{ width: '100%' }}>
              <div>
                <Text type="secondary" style={{ fontSize: 12 }}>补充 AI 可能不知道的业务规则（每行一条）</Text>
                <TextArea
                  placeholder="示例：&#10;部分退款后订单状态保持「已发货」&#10;金额超过500元需要财务审批&#10;同一订单最多退款3次"
                  rows={3}
                  onChange={e => setSettings(s => ({ ...s, business_rules: e.target.value }))}
                />
              </div>
            </Space>
          ),
        }]}
        style={{ marginBottom: 16 }}
      />

      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '12px 16px', background: 'rgba(124,172,248,0.06)', borderRadius: 8,
      }}>
        <Space size="large">
          <Text type="secondary" style={{ fontSize: 12 }}>
            {charCount.toLocaleString()} 字符
          </Text>
          <Text type="secondary" style={{ fontSize: 12 }}>
            预估 ~{(tokenEstimate / 1000).toFixed(0)}k token
          </Text>
        </Space>
        <Button
          type="primary"
          icon={<SendOutlined />}
          onClick={handleSubmit}
          loading={loading}
          disabled={!content.trim() || !title.trim()}
        >
          开始分析
        </Button>
      </div>

      {charCount > 200_000 && (
        <Alert
          type="warning"
          message={`文档超过 200,000 字符上限（当前 ${charCount.toLocaleString()}），请拆分后分批提交`}
          style={{ marginTop: 12 }}
          showIcon
        />
      )}
    </div>
  )
}
