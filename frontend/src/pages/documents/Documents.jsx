import { useState, useEffect, useCallback } from 'react'
import { useParams } from 'react-router-dom'
import {
  Button, Card, Table, Tag, Space, Typography, Modal, Form, Input, Select,
  message, Drawer, Popconfirm, Tooltip,
} from 'antd'
import {
  PlusOutlined, FileTextOutlined, DeleteOutlined, EyeOutlined,
  RobotOutlined, LoadingOutlined, CopyOutlined, DownloadOutlined, CodeOutlined,
  EditOutlined, UndoOutlined,
} from '@ant-design/icons'
import { marked } from 'marked'
import { api, getValidToken } from '../../utils/request'
import { copyToClipboard } from '../../utils/clipboard'

const { Text } = Typography
const { TextArea } = Input

const DOC_TYPE_LABELS = { manual: '操作手册', acceptance: '验收文档', demo: '演示文档' }
const DOC_TYPE_COLORS = { manual: 'blue', acceptance: 'cyan', demo: 'purple' }
const LANG_OPTIONS = [
  { value: 'zh', label: '中文' },
  { value: 'en', label: 'English' },
]
const LANG_LABELS = { zh: '中文', en: 'EN' }

// marked 配置
marked.setOptions({ breaks: true, gfm: true })

// 数正文里有几张截图 —— 「优化文字保留截图」这句话得有个数字撑着
const countImages = (md) => (md || '').match(/!\[[^\]]*\]\([^)]+\)/g)?.length || 0

export default function Documents() {
  const { projectId } = useParams()
  const [docs, setDocs] = useState([])
  const [loading, setLoading] = useState(false)
  const [genOpen, setGenOpen] = useState(false)
  const [ccForm] = Form.useForm()
  const [regenDocId, setRegenDocId] = useState(null)
  const [regenFeedback, setRegenFeedback] = useState('')
  const [taskResult, setTaskResult] = useState(null)

  // 平台生成
  const [platGenerating, setPlatGenerating] = useState(false)
  const [platContent, setPlatContent] = useState('')
  const [platProgress, setPlatProgress] = useState([])

  // 文档预览抽屉
  const [previewDoc, setPreviewDoc] = useState(null)
  const [previewOpen, setPreviewOpen] = useState(false)

  // 优化文字（保留截图，不碰被测系统）
  const [optDoc, setOptDoc] = useState(null)
  const [optFeedback, setOptFeedback] = useState('')
  const [optRunning, setOptRunning] = useState(false)
  const [optChars, setOptChars] = useState(0)

  const fetchDocs = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    try { const res = await api.get(`/projects/${projectId}/documents`); setDocs(res.data || []) }
    catch { /* */ } finally { setLoading(false) }
  }, [projectId])

  useEffect(() => { fetchDocs() }, [fetchDocs])

  const loadDoc = async (docId) => {
    try {
      const res = await api.get(`/projects/${projectId}/documents/${docId}`)
      setPreviewDoc(res.data)
      setPreviewOpen(true)
    } catch { /* */ }
  }

  const handleDelete = async (id) => {
    await api.del(`/projects/${projectId}/documents/${id}`).catch(() => {})
    message.success('已删除')
    if (previewDoc?.id === id) { setPreviewOpen(false); setPreviewDoc(null) }
    fetchDocs()
  }

  // 平台直接生成
  const handlePlatGenerate = async () => {
    try {
      const v = await ccForm.validateFields()
      setPlatGenerating(true); setPlatContent(''); setPlatProgress([])
      api.stream(`/projects/${projectId}/documents/generate-with-screenshots`, {
        systemUrl: v.systemUrl, username: v.username, password: v.password,
        title: v.title, docType: v.docType || 'manual',
        languages: v.languages || ['zh'],
        modules: v.modules || undefined, audience: v.audience || undefined,
        businessContext: v.businessContext || undefined,
        feedback: regenFeedback || undefined,
        docId: regenDocId || undefined,
      }, {
        onChunk: (data) => {
          if (data.type === 'skill_start') setPlatProgress(prev => [...prev, `🚀 Skill: ${data.skill}`])
          if (data.type === 'step_start') setPlatProgress(prev => [...prev, `⏳ Step ${data.step}: ${data.title}`])
          if (data.type === 'step_done') setPlatProgress(prev => [...prev, `✅ Step ${data.step}: ${data.summary}`])
          if (data.type === 'screenshot') setPlatProgress(prev => [...prev, `📸 ${data.page}`])
          if (data.type === 'chunk' && data.content) setPlatContent(prev => prev + data.content)
          if (data.type === 'error') { message.error(data.message); setPlatGenerating(false) }
        },
        onDone: (data) => {
          message.success('文档已生成')
          setPlatGenerating(false); setGenOpen(false); ccForm.resetFields()
          setRegenDocId(null); setRegenFeedback('')
          fetchDocs()
          if (data?.docId) loadDoc(data.docId)
        },
        onError: (msg) => { message.error(msg); setPlatGenerating(false) },
      })
    } catch { /* */ }
  }



  // 优化文字：保留原截图，只按修改意见重写正文。
  // 和「重新生成」是两条路 —— 那条会拿账号真登录被测系统重新截一遍图。
  const handleOptimize = async () => {
    if (!optDoc) return
    const docId = optDoc.id
    setOptRunning(true); setOptChars(0)
    api.stream(`/projects/${projectId}/documents/${docId}/optimize`, { feedback: optFeedback }, {
      onChunk: (data) => {
        if (data.type === 'chunk' && data.content) setOptChars(prev => prev + data.content.length)
        if (data.type === 'error') { message.error(data.message); setOptRunning(false) }
      },
      onDone: async (data) => {
        setOptRunning(false); setOptDoc(null); setOptFeedback('')
        fetchDocs()
        if (previewOpen && previewDoc?.id) loadDoc(previewDoc.id)
        const lost = data?.imagesLost || 0
        if (lost > 0) {
          // 「保留截图」是这条路唯一的承诺。没兑现就得当面说，并且把撤销递到手上
          Modal.confirm({
            title: `优化后少了 ${lost} 张截图`,
            content: `优化前 ${data.imagesBefore} 张，现在 ${data.imagesAfter} 张。截图是真登录系统截出来的，丢了只能走「重新生成」重截。`,
            okText: '撤销这次优化',
            okButtonProps: { danger: true },
            cancelText: '先留着',
            onOk: () => handleRevert(docId),
          })
        } else {
          message.success(`已优化，${data?.imagesAfter ?? 0} 张截图都在`)
        }
      },
      onError: (msg) => { message.error(msg); setOptRunning(false) },
    })
  }

  const handleRevert = async (docId) => {
    try {
      await api.post(`/projects/${projectId}/documents/${docId}/revert-optimize`)
      message.success('已退回优化前的版本')
      fetchDocs()
      if (previewOpen && previewDoc?.id === docId) loadDoc(docId)
    } catch { /* 后端会给出原因 */ }
  }

  const openRegen = async (docRecord) => {
    setGenOpen(true); setTaskResult(null); setRegenDocId(docRecord.id)
    setRegenFeedback('')
    // 回填基本信息
    ccForm.setFieldsValue({ title: docRecord.title, docType: docRecord.docType, languages: [docRecord.language || 'zh'] })
    // 加载详情拿 genConfig 回填表单
    try {
      const res = await api.get(`/projects/${projectId}/documents/${docRecord.id}`)
      const cfg = res.data?.genConfig
      if (cfg) {
        ccForm.setFieldsValue({
          systemUrl: cfg.systemUrl, username: cfg.username, password: cfg.password,
          modules: cfg.modules, audience: cfg.audience, businessContext: cfg.businessContext,
        })
      }
    } catch { /* */ }
  }

  const handleCCGenerate = async () => {
    try {
      const v = await ccForm.validateFields()
      const res = await api.post(`/projects/${projectId}/documents/tasks`, {
        _host: window.location.hostname + ':8756',
        systemUrl: v.systemUrl, username: v.username, password: v.password,
        title: v.title, docType: v.docType || 'manual',
        modules: v.modules || undefined, audience: v.audience || undefined,
        businessContext: v.businessContext || undefined,
      })
      setTaskResult(res.data)
    } catch { /* */ }
  }

  const columns = [
    {
      title: '文档标题', dataIndex: 'title', ellipsis: true,
      render: (t, r) => (
        <Space>
          <FileTextOutlined style={{ color: DOC_TYPE_COLORS[r.docType] === 'blue' ? '#4e8af0' : DOC_TYPE_COLORS[r.docType] === 'green' ? '#0ea5a0' : '#7c5cbf' }} />
          <a onClick={() => loadDoc(r.id)} style={{ fontWeight: 500 }}>{t}</a>
        </Space>
      ),
    },
    { title: '类型', dataIndex: 'docType', width: 100, render: (t) => <Tag color={DOC_TYPE_COLORS[t]}>{DOC_TYPE_LABELS[t] || t}</Tag> },
    { title: '语种', dataIndex: 'language', width: 60, render: (l) => LANG_LABELS[l] || l },
    { title: '状态', dataIndex: 'status', width: 70, render: (s) => s === 'published' ? <Tag color="cyan">已生成</Tag> : <Tag>草稿</Tag> },
    { title: '生成时间', dataIndex: 'createdAt', width: 150, render: (t) => <Text type="secondary">{t?.slice(0, 16).replace('T', ' ')}</Text> },
    {
      title: '操作', width: 320,
      render: (_, r) => (
        <Space size={4}>
          <Button type="link" size="small" icon={<EyeOutlined />} onClick={() => loadDoc(r.id)}>查看</Button>
          {/* 两条路的代价差很多，按钮上就得能分出来，别让人靠猜 */}
          <Tooltip title="保留现有截图，只按你的意见重写文字。不碰被测系统，不用再填账号">
            <Button type="text" size="small" icon={<EditOutlined />} onClick={() => { setOptDoc(r); setOptFeedback('') }}>优化文字</Button>
          </Tooltip>
          <Tooltip title="拿账号重新登录被测系统、重新截一遍图、整篇重做">
            <Button type="text" size="small" icon={<RobotOutlined />} onClick={() => openRegen(r)}>重新生成</Button>
          </Tooltip>
          {r.canRevert && (
            <Popconfirm title="退回上一次优化前的正文？" onConfirm={() => handleRevert(r.id)}>
              <Tooltip title="撤销上一次「优化文字」"><Button type="text" size="small" icon={<UndoOutlined />} /></Tooltip>
            </Popconfirm>
          )}
          <Popconfirm title="确认删除此文档？" onConfirm={() => handleDelete(r.id)}>
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div>
      <div style={{ marginBottom: 12 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: '#1d2129' }}>文档管理</h2>
        <span style={{ fontSize: 13, color: '#86909c' }}>
          自动截图 + AI 生成操作手册、演示文档、验收文档，支持多语种
        </span>
      </div>

      <Card size="small" style={{ marginBottom: 12, background: 'rgba(0,0,0,0.02)' }}>
        <div style={{ fontSize: 13, lineHeight: 2 }}>
          <b>文档生成流程：</b>
          <span style={{ marginLeft: 12 }}>① 填写系统地址和账号 → ② 自动打开系统截图 → ③ AI 根据截图写文档 → ④ 预览/导出</span>
        </div>
      </Card>

      <div style={{ marginBottom: 12 }}>
        <Button type="primary" icon={<PlusOutlined />} onClick={() => { setGenOpen(true); setTaskResult(null); setRegenDocId(null); setRegenFeedback(''); ccForm.resetFields() }}>
          生成文档
        </Button>
      </div>

      {docs.length === 0 && !loading ? (
        <Card style={{ textAlign: 'center', padding: '40px 0' }}>
          <FileTextOutlined style={{ fontSize: 40, color: 'rgba(0,0,0,0.25)' }} />
          <div style={{ marginTop: 12, color: '#86909c' }}>暂无文档</div>
          <div style={{ marginTop: 4, color: '#c9cdd4', fontSize: 12 }}>点击上方「生成文档」按钮，填写系统信息即可自动生成</div>
        </Card>
      ) : (
        <Table rowKey="id" columns={columns} dataSource={docs} loading={loading} pagination={false} size="small" />
      )}

      {/* 生成弹窗 */}
      <Modal
        title={regenDocId ? '重新生成文档' : '生成文档'}
        open={genOpen}
        onCancel={() => { if (!platGenerating) { setGenOpen(false); setRegenFeedback('') } }}
        width={560}
        footer={!taskResult && !platGenerating ? (
          regenDocId ? [
            <Button key="cancel" onClick={() => setGenOpen(false)}>取消</Button>,
            <Button key="gen" type="primary" icon={<RobotOutlined />} onClick={handlePlatGenerate}>重新生成</Button>,
          ] : [
            <Button key="cancel" onClick={() => setGenOpen(false)}>取消</Button>,
            <Button key="cc" icon={<CodeOutlined />} onClick={handleCCGenerate}>Claude Code 命令</Button>,
            <Button key="gen" type="primary" icon={<RobotOutlined />} onClick={handlePlatGenerate}>平台直接生成</Button>,
          ]
        ) : null}
      >
        {!taskResult && !platGenerating ? (
          <Form form={ccForm} layout="vertical" style={{ marginTop: 12 }}>
            {regenDocId && (
              <Form.Item label="修改意见" style={{ marginBottom: 16 }}>
                <TextArea
                  rows={2}
                  value={regenFeedback}
                  onChange={(e) => setRegenFeedback(e.target.value)}
                  placeholder="可选，AI 生成时会参考"
                />
              </Form.Item>
            )}
            <Form.Item name="title" label="文档标题" rules={[{ required: true, message: '请输入' }]} style={{ marginBottom: 20 }}>
              <Input placeholder="测试管理平台操作手册" style={{ fontSize: 16, fontFamily: '"Comic Sans MS", "Noto Sans SC", "PingFang SC", cursive', fontWeight: 600, height: 42 }} />
            </Form.Item>
            <Form.Item name="systemUrl" label="系统地址" rules={[{ required: true, message: '请输入' }]}>
              <Input placeholder="http://192.168.51.108:5173" />
            </Form.Item>
            {/* 「平台直接生成」会拿这组账号真登录被测系统、点侧栏导航、还会点主内容区
                的「新增/创建/添加/新建」按钮把弹窗截下来（截完点取消关掉，不填不提交）。
                原来这里只管要账号密码，一个字没说要去点什么 —— 人把生产账号填进来
                之前，有权知道平台会在他的系统上做哪些动作。 */}
            <div style={{
              margin: '-8px 0 16px', padding: '8px 12px', borderRadius: 10, lineHeight: 1.8,
              background: 'rgba(250,140,22,0.06)', border: '1px solid rgba(250,140,22,0.2)',
              fontSize: 12, color: '#4e5969',
            }}>
              「平台直接生成」会用这组账号<b>真登录这个系统</b>：点侧栏各模块截图，
              并点主内容区的「新增/创建」按钮把弹窗截下来（截完即取消，<b>不填表、不提交</b>）。
              <br />
              别用生产账号；要完全不碰系统就走「Claude Code 命令」，由你本地掌控每一步。
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <Form.Item name="username" label="账号" rules={[{ required: true, message: '请输入' }]}>
                <Input placeholder="admin" />
              </Form.Item>
              <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入' }]}>
                <Input.Password placeholder="admin123" />
              </Form.Item>
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
              <Form.Item name="docType" label="文档类型" initialValue="manual">
                <Select options={Object.entries(DOC_TYPE_LABELS).map(([k, v]) => ({ value: k, label: v }))} />
              </Form.Item>
              <Form.Item name="languages" label="语种" initialValue={['zh']}>
                <Select mode="multiple" options={LANG_OPTIONS} placeholder="选择语种" />
              </Form.Item>
            </div>
            <Form.Item name="modules" label="文档范围">
              <TextArea rows={2} placeholder="用户管理、项目管理&#10;不填则生成全部功能" />
            </Form.Item>
            <Form.Item name="audience" label="目标读者">
              <Input placeholder="新入职测试工程师" />
            </Form.Item>
            <Form.Item name="businessContext" label="业务说明">
              <TextArea rows={3} placeholder="可选，系统介绍或 PRD 摘要" />
            </Form.Item>
          </Form>
        ) : taskResult ? (
          <div>
            <div style={{ marginBottom: 16, fontSize: 13, color: '#0ea5a0' }}>任务已保存</div>
            {taskResult.instructions?.map((step, i) => <div key={i} style={{ fontSize: 13, lineHeight: 2 }}>{step}</div>)}
            <div style={{ margin: '16px 0', padding: '12px 16px', background: '#1e1e1e', borderRadius: 12 }}>
              <div style={{ fontSize: 11, color: '#86909c', marginBottom: 4 }}>复制到 Claude Code：</div>
              <div style={{ color: '#d4d4d4', fontFamily: 'var(--font-mono)', fontSize: 13 }}>{taskResult.command}</div>
            </div>
            <div style={{ display: 'flex', justifyContent: 'space-between' }}>
              <Button onClick={() => setTaskResult(null)}>返回修改</Button>
              <Button type="primary" icon={<CopyOutlined />} onClick={() => { copyToClipboard(taskResult.command); message.success('已复制') }}>复制命令</Button>
            </div>
          </div>
        ) : (
          <div>
            <div style={{ textAlign: 'center', marginBottom: 16 }}>
              <LoadingOutlined style={{ fontSize: 24 }} />
              <div style={{ marginTop: 8, fontWeight: 500 }}>正在生成文档...</div>
            </div>
            {/* platContent 一直在攒流式正文，但从来没显示过 —— 一篇文档要写好几分钟，
                进度只有几行 step，人看不出到底在不在动。至少把字数报出来。 */}
            {platContent.length > 0 && (
              <div style={{ textAlign: 'center', fontSize: 12, color: '#4e5969', marginBottom: 8 }}>
                已生成 {platContent.length} 字
              </div>
            )}
            {platProgress.length > 0 && (
              <div style={{ padding: '8px 12px', background: 'rgba(0,0,0,0.02)', borderRadius: 12, maxHeight: 150, overflow: 'auto', marginBottom: 12 }}>
                {platProgress.map((p, i) => <div key={i} style={{ fontSize: 12, color: '#4e5969', padding: '2px 0' }}>{p}</div>)}
              </div>
            )}
          </div>
        )}
      </Modal>

      {/* 优化文字 —— 保留截图那条路 */}
      <Modal
        title="优化文字"
        open={!!optDoc}
        onCancel={() => { if (!optRunning) { setOptDoc(null); setOptFeedback('') } }}
        width={520}
        okText="开始优化"
        confirmLoading={optRunning}
        onOk={handleOptimize}
        cancelButtonProps={{ disabled: optRunning }}
      >
        <div style={{
          margin: '4px 0 14px', padding: '8px 12px', borderRadius: 10, lineHeight: 1.8,
          background: 'rgba(14,165,160,0.06)', border: '1px solid rgba(14,165,160,0.2)',
          fontSize: 12, color: '#4e5969',
        }}>
          <b>不会碰被测系统</b>：沿用文档里现有的 {countImages(optDoc?.content)} 张截图，只重写文字，也不用再填账号。
          <br />
          要换截图（页面改版了、要补新模块）请走「重新生成」——那条会重新登录、重新截图。
          <br />
          改完可以一键撤销，退回优化前的正文。
        </div>
        <TextArea
          rows={4}
          value={optFeedback}
          onChange={(e) => setOptFeedback(e.target.value)}
          disabled={optRunning}
          placeholder={'哪儿不对就直接说，例如：\n第 3 节步骤太笼统，把每个字段填什么写出来\n开头加一段这个系统是干什么的\n不填则按格式模板做通用润色'}
        />
        {optRunning && (
          <div style={{ marginTop: 12, fontSize: 12, color: '#4e5969' }}>
            <LoadingOutlined /> 正在重写…… 已生成 {optChars} 字（写完才会覆盖原文，中途关掉不影响原文档）
          </div>
        )}
      </Modal>

      {/* 文档预览抽屉 */}
      <Drawer
        title={previewDoc?.title || ''}
        open={previewOpen}
        onClose={() => setPreviewOpen(false)}
        width={1100}
        extra={previewDoc?.content && (
          <Space>
            {/* 读着读着发现哪句不对，就在这儿改 —— 不用退出去、更不用重新截一遍图 */}
            <Button size="small" icon={<EditOutlined />} onClick={() => { setOptDoc(previewDoc); setOptFeedback('') }}>优化文字</Button>
            {previewDoc?.canRevert && (
              <Popconfirm title="退回上一次优化前的正文？" onConfirm={() => handleRevert(previewDoc.id)}>
                <Button size="small" icon={<UndoOutlined />}>撤销优化</Button>
              </Popconfirm>
            )}
            <Button size="small" icon={<RobotOutlined />} onClick={() => {
              setPreviewOpen(false)
              openRegen(previewDoc)
            }}>重新生成</Button>
            <Button size="small" icon={<CopyOutlined />} onClick={() => { copyToClipboard(previewDoc.content); message.success('已复制') }}>复制</Button>
            <Button size="small" icon={<DownloadOutlined />} onClick={() => downloadFromApi(`/projects/${projectId}/documents/${previewDoc.id}/export-zip`, `${previewDoc.title}.zip`)}>下载</Button>
            <Button size="small" type="primary" icon={<DownloadOutlined />} onClick={() => downloadFromApi(`/projects/${projectId}/documents/${previewDoc.id}/export-html`, `${previewDoc.title}.html`)}>导出 HTML</Button>
          </Space>
        )}
      >
        {previewDoc?.content && (
          <DocPreviewWithToc content={previewDoc.content} />
        )}
        <style>{`
          .markdown-body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, "PingFang SC", "Microsoft YaHei", sans-serif;
            color: #1f2328;
            word-wrap: break-word;
          }
          .markdown-body > *:first-child { margin-top: 0 !important; }

          /* Headings */
          .markdown-body h1 {
            font-size: 1.75em; font-weight: 700; line-height: 1.3;
            margin: 32px 0 16px; padding-bottom: 10px;
            border-bottom: 2px solid #d1d9e0;
          }
          .markdown-body h2 {
            font-size: 1.35em; font-weight: 600; line-height: 1.35;
            margin: 28px 0 14px; padding-bottom: 8px;
            border-bottom: 1px solid #d8dee4;
            color: #1f2328;
          }
          .markdown-body h3 {
            font-size: 1.15em; font-weight: 600; line-height: 1.4;
            margin: 22px 0 10px; color: #25292e;
          }
          .markdown-body h4 {
            font-size: 1em; font-weight: 600; line-height: 1.4;
            margin: 18px 0 8px; color: #32383f;
          }

          /* Paragraph */
          .markdown-body p { margin: 8px 0 12px; }

          /* Bold / Emphasis */
          .markdown-body strong { font-weight: 600; color: #1f2328; }
          .markdown-body em { color: #656d76; font-style: italic; }
          /* Image caption: em right after img (Typora pattern: *图：xxx*) */
          .markdown-body img + br + em,
          .markdown-body p > em:only-child {
            display: block; text-align: center; font-size: 0.85em;
            color: #656d76; margin: -4px 0 16px;
          }

          /* HR */
          .markdown-body hr {
            border: none; height: 2px;
            background: #d8dee4; margin: 28px 0;
          }

          /* Table */
          .markdown-body table {
            border-collapse: collapse; width: 100%;
            margin: 16px 0; overflow-x: auto; display: block;
            font-variant-numeric: tabular-nums;
          }
          .markdown-body thead { background: #f6f8fa; }
          .markdown-body th {
            font-weight: 600; text-align: left;
            padding: 10px 14px; font-size: 13px;
            border: 1px solid #d0d7de; background: #f6f8fa;
          }
          .markdown-body td {
            padding: 8px 14px; font-size: 13px;
            border: 1px solid #d0d7de; vertical-align: top;
          }
          .markdown-body tbody tr:nth-child(even) { background: #f6f8fa; }

          /* Blockquote */
          .markdown-body blockquote {
            border-left: 4px solid #0ea5a0; padding: 12px 20px;
            margin: 16px 0; background: var(--green-bg);
            color: #1f2328; border-radius: 0 6px 6px 0;
          }
          .markdown-body blockquote p { margin: 4px 0; }
          .markdown-body blockquote strong { color: #0ea5a0; }

          /* Inline code */
          .markdown-body code {
            background: #eff1f3; padding: 2px 7px;
            border-radius: 4px; font-size: 0.9em;
            font-family: var(--font-mono);
          }
          /* Code block */
          .markdown-body pre {
            background: #161b22; color: #e6edf3;
            border-radius: 8px; padding: 16px 20px;
            margin: 16px 0; overflow-x: auto;
            line-height: 1.5;
          }
          .markdown-body pre code {
            background: none; padding: 0; border-radius: 0;
            font-size: 13px; color: inherit;
          }

          /* Image */
          .markdown-body img {
            max-width: 100%; border-radius: 8px;
            border: 1px solid #d0d7de;
            margin: 12px 0 4px;
            box-shadow: 0 1px 4px rgba(0,0,0,0.06);
          }

          /* Lists */
          .markdown-body ul, .markdown-body ol {
            padding-left: 24px; margin: 8px 0;
          }
          .markdown-body li { margin: 4px 0; line-height: 1.7; }
          .markdown-body li > p { margin: 2px 0; }
          .markdown-body ul ul, .markdown-body ol ul,
          .markdown-body ul ol, .markdown-body ol ol {
            margin: 2px 0;
          }

          /* Links */
          .markdown-body a { color: #0969da; text-decoration: none; }
          .markdown-body a:hover { text-decoration: underline; }
        `}</style>
      </Drawer>
    </div>
  )
}

function DocPreviewWithToc({ content }) {
  const html = marked.parse(content)
  const headings = []
  const htmlWithIds = html.replace(/<h([1-3])([^>]*)>(.*?)<\/h[1-3]>/gi, (match, level, attrs, text) => {
    const id = `heading-${headings.length}`
    const plainText = text.replace(/<[^>]*>/g, '').replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'")
    headings.push({ id, level: parseInt(level), text: plainText })
    return `<h${level}${attrs} id="${id}">${text}</h${level}>`
  })

  const scrollTo = (id) => {
    const el = document.getElementById(id)
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div style={{ display: 'flex', gap: 0, height: '100%', margin: '-24px', overflow: 'hidden' }}>
      {headings.length > 0 && (
        <div className="doc-toc" style={{
          width: 220, minWidth: 220, borderRight: '1px solid rgba(0,0,0,0.05)',
          padding: '16px 0', overflowY: 'auto', fontSize: 13, background: 'transparent',
        }}>
          <div style={{ padding: '0 16px 8px', fontWeight: 600, fontSize: 12, color: '#86909c', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
            目录
          </div>
          {headings.map((h) => (
            <div
              key={h.id}
              onClick={() => scrollTo(h.id)}
              style={{
                padding: '4px 16px', paddingLeft: 16 + (h.level - 1) * 14,
                cursor: 'pointer', color: '#333', lineHeight: 1.6,
                whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                fontWeight: h.level === 1 ? 600 : 400,
                fontSize: h.level === 1 ? 13 : 12,
              }}
              onMouseEnter={(e) => { e.target.style.background = 'rgba(14,165,160,0.08)'; e.target.style.color = '#0ea5a0' }}
              onMouseLeave={(e) => { e.target.style.background = ''; e.target.style.color = '#333' }}
            >
              {h.text}
            </div>
          ))}
        </div>
      )}
      <div style={{ flex: 1, overflowY: 'auto', padding: 24 }}>
        <div
          className="markdown-body"
          style={{ fontSize: 14, lineHeight: 1.8 }}
          dangerouslySetInnerHTML={{ __html: htmlWithIds }}
        />
      </div>
    </div>
  )
}

async function downloadFromApi(url, filename) {
  const token = await getValidToken()
  const res = await fetch(`/api${url}`, { headers: token ? { Authorization: `Bearer ${token}` } : {} })
  if (!res.ok) { message.error('下载失败'); return }
  const blob = await res.blob()
  const a = document.createElement('a')
  a.href = URL.createObjectURL(blob)
  a.download = filename; a.click()
  URL.revokeObjectURL(a.href)
}
