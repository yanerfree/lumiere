/**
 * 项目 Skill 分区 —— 客户端(Claude Code)侧执行的 skill。
 *
 * 跟同页上方的平台内置 tb-* 是两类东西：内置那批平台自己拿去喂后端 LLM、要绑模型档位；
 * 这里的跑在开发者机器的 Claude Code 里，平台只做存取。所以刻意分区呈现，不混一个列表。
 */
import { useCallback, useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import {
  Alert, Button, Card, Drawer, Empty, Input, Modal, Popconfirm, Segmented,
  Space, Spin, Table, Tag, Tooltip, Typography, Upload, message,
} from 'antd'
import {
  ApiOutlined, CloudUploadOutlined, DeleteOutlined, DownloadOutlined,
  EditOutlined, HistoryOutlined, InboxOutlined, PlusOutlined, RollbackOutlined,
  SaveOutlined, TeamOutlined,
} from '@ant-design/icons'
import { api, getValidToken } from '../../utils/request'

const { Text, Paragraph } = Typography

const MCP_SNIPPET = `# 在任意项目的 Claude Code 里：
「把我 .claude/skills 下的 feature-verify 传到 testBench」   → tb_push_skill
「看看 testBench 上有哪些 skill 能用」                        → tb_list_skills
「把 feature-verify 拉到本地」                                → tb_pull_skill`

// 粘贴框的起手模板 —— 让人一眼知道 frontmatter 要写什么，name 是从这里读的
const SKILL_TEMPLATE = `---
name: my-skill
description: 一句话说清这个 skill 干什么、什么时候该触发它
---

# 标题

> 目标：想让 AI 达成什么。

## 步骤
1. 第一步做什么
2. 第二步做什么

## 红线
- 不许做的事写在这里
`

export default function ProjectSkillSection() {
  const { projectId } = useParams()
  const [scope, setScope] = useState('own')      // own = 本项目 | shared = 全平台共享
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)
  const [addOpen, setAddOpen] = useState(false)
  const [addTab, setAddTab] = useState('paste')   // paste = 粘贴正文（最省事）| upload = 传压缩包
  const [uploading, setUploading] = useState(false)
  const [pasteContent, setPasteContent] = useState('')
  const [pasteVisibility, setPasteVisibility] = useState('public')
  const [creating, setCreating] = useState(false)

  const [editing, setEditing] = useState(null)   // { name, content }
  const [saving, setSaving] = useState(false)
  const [versions, setVersions] = useState(null) // { name, list, current }
  const [howToOpen, setHowToOpen] = useState(false)

  const load = useCallback(async () => {
    if (!projectId) return
    setLoading(true)
    try {
      const url = scope === 'own'
        ? `/projects/${projectId}/skills`
        : `/projects/${projectId}/skills/shared`
      const res = await api.get(url)
      setRows(res.data || [])
    } catch {
      message.error('加载 Skill 列表失败')
    } finally {
      setLoading(false)
    }
  }, [projectId, scope])

  useEffect(() => { load() }, [load])

  const handleUpload = async (file) => {
    setUploading(true)
    try {
      const formData = new FormData()
      formData.append('file', file)
      const token = await getValidToken()
      const res = await fetch(`/api/projects/${projectId}/skills/upload`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      })
      const data = await res.json()
      if (!res.ok) {
        message.error(data?.error?.message || '上传失败')
      } else {
        const d = data.data
        message.success(`${d.name} ${d.created ? '已上传' : `已覆盖，升到 v${d.version}`}`)
        closeAdd()
        setScope('own')
        load()
      }
    } catch {
      message.error('上传失败')
    } finally {
      setUploading(false)
    }
    return false  // 阻止 antd 自己发请求
  }

  const closeAdd = () => {
    setAddOpen(false)
    setPasteContent('')
    setPasteVisibility('public')
  }

  /** 粘贴正文直接建 —— 单文件 skill 的最短路径，不用打包。name 从 frontmatter 读。 */
  const handleCreateFromPaste = async () => {
    if (!pasteContent.trim()) {
      message.warning('先把 SKILL.md 内容粘进来')
      return
    }
    setCreating(true)
    try {
      const res = await api.post(`/projects/${projectId}/skills`, {
        content: pasteContent,
        visibility: pasteVisibility,
      })
      const d = res.data
      message.success(`${d.name} ${d.created ? '已添加' : `已覆盖，升到 v${d.version}`}`)
      closeAdd()
      setScope('own')
      load()
    } catch (e) {
      // 最常见的两种：frontmatter 没写 name，或者 name 不合法 —— 后端的报错已经说清了原因
      message.error(e?.message || '添加失败')
    } finally {
      setCreating(false)
    }
  }

  const handleEdit = async (name) => {
    try {
      const res = await api.get(`/projects/${projectId}/skills/${name}`)
      setEditing({ name, content: res.data.content, visibility: res.data.visibility })
    } catch { message.error('加载失败') }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.put(`/projects/${projectId}/skills/${editing.name}`, {
        content: editing.content,
        visibility: editing.visibility,
      })
      message.success('已保存，旧版本已留档')
      setEditing(null)
      load()
    } catch (e) { message.error(e?.message || '保存失败') }
    finally { setSaving(false) }
  }

  const handleDelete = async (name) => {
    try {
      await api.del(`/projects/${projectId}/skills/${name}`)
      message.success(`${name} 已删除`)
      load()
    } catch (e) { message.error(e?.message || '删除失败') }
  }

  const handleDownload = async (row) => {
    try {
      const url = row.own
        ? `/projects/${projectId}/skills/${row.name}/bundle`
        : `/projects/${projectId}/skills/shared/${row.id}/bundle`
      const blob = await api.download(url)
      const link = document.createElement('a')
      link.href = URL.createObjectURL(blob)
      link.download = `${row.name}.tar.gz`
      link.click()
      URL.revokeObjectURL(link.href)
    } catch { message.error('下载失败') }
  }

  const openVersions = async (name) => {
    try {
      const res = await api.get(`/projects/${projectId}/skills/${name}/versions`)
      setVersions({ name, list: res.data || [], current: res.currentVersion })
    } catch { message.error('加载版本失败') }
  }

  const handleRollback = async (name, version) => {
    try {
      await api.post(`/projects/${projectId}/skills/${name}/rollback/${version}`)
      message.success(`已回滚到 v${version}`)
      setVersions(null)
      load()
    } catch (e) { message.error(e?.message || '回滚失败') }
  }

  const columns = [
    {
      title: 'Skill',
      dataIndex: 'name',
      render: (name, row) => (
        <div>
          <Space size={6}>
            <Text code style={{ fontSize: 13 }}>{name}</Text>
            <Tag color="cyan" style={{ fontSize: 11 }}>v{row.version}</Tag>
            {row.visibility === 'public'
              ? <Tag style={{ fontSize: 11 }} icon={<TeamOutlined />}>全平台可取用</Tag>
              : <Tag style={{ fontSize: 11 }}>仅本项目</Tag>}
            {row.platformTools?.length > 0 && (
              <Tooltip title={`声明了平台 MCP 工具：${row.platformTools.join(', ')} —— 取用方项目也必须连上 testBench MCP 才能跑通`}>
                <Tag color="orange" style={{ fontSize: 11 }} icon={<ApiOutlined />}>依赖平台工具</Tag>
              </Tooltip>
            )}
          </Space>
          <div style={{ fontSize: 12, color: '#86909c', marginTop: 2 }}>
            {row.description || <Text type="secondary">（SKILL.md 里没写 description）</Text>}
          </div>
        </div>
      ),
    },
    {
      title: '来源',
      width: 150,
      render: (_, row) => (
        <div style={{ fontSize: 12 }}>
          <div>{row.own ? <Tag color="cyan">本项目</Tag> : <Tag>{row.sourceProject || '其它项目'}</Tag>}</div>
          <Text type="secondary" style={{ fontSize: 11 }}>
            {{ mcp: 'Claude Code 推送', upload: '打包上传', ui: '页面编辑' }[row.source] || row.source}
          </Text>
        </div>
      ),
    },
    {
      title: '文件',
      width: 90,
      render: (_, row) => (
        <Text type="secondary" style={{ fontSize: 12 }}>
          SKILL.md{row.fileCount > 0 ? ` + ${row.fileCount}` : ''}
        </Text>
      ),
    },
    {
      title: '操作',
      width: 230,
      render: (_, row) => (
        <Space size={4}>
          <Button size="small" icon={<DownloadOutlined />} onClick={() => handleDownload(row)}>下载</Button>
          {row.own && (
            <>
              <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(row.name)}>编辑</Button>
              <Button size="small" icon={<HistoryOutlined />} onClick={() => openVersions(row.name)} />
              <Popconfirm
                title={`删除 ${row.name}？`}
                description="连历史版本一起删，其它项目也将取不到"
                okText="删除" cancelText="取消" okButtonProps={{ danger: true }}
                onConfirm={() => handleDelete(row.name)}
              >
                <Button size="small" danger icon={<DeleteOutlined />} />
              </Popconfirm>
            </>
          )}
        </Space>
      ),
    },
  ]

  return (
    <Card
      size="small"
      style={{ marginTop: 24, borderLeft: '3px solid #7c5cff' }}
      title={
        <Space>
          <CloudUploadOutlined style={{ color: '#7c5cff' }} />
          <span>项目 Skill</span>
          <Text type="secondary" style={{ fontSize: 12, fontWeight: 400 }}>
            客户端执行 —— 跑在你机器的 Claude Code 里，平台只负责存取和分发
          </Text>
        </Space>
      }
      extra={
        <Space>
          <Button size="small" onClick={() => setHowToOpen(true)}>怎么用</Button>
          <Button size="small" type="primary" icon={<PlusOutlined />} onClick={() => { setAddTab('paste'); setAddOpen(true) }}>
            添加 Skill
          </Button>
        </Space>
      }
    >
      <Alert
        type="info" showIcon closable style={{ marginBottom: 12 }}
        message="跟上面的平台 Skill 有什么不同？"
        description={
          <div style={{ fontSize: 12, lineHeight: 1.9 }}>
            上面的 <Text code>tb-*</Text> 是<b>平台侧执行</b>：后端把 SKILL.md 当 prompt 喂给 LLM，每个都要在「AI 能力→模型」里绑模型档位。<br />
            这里的是<b>客户端侧执行</b>：跑在开发者机器的 Claude Code 里，用 Bash / Edit / Playwright 等本地工具，平台永不执行它们，也不占模型档位。
          </div>
        }
      />

      <Space style={{ marginBottom: 12 }}>
        <Segmented
          size="small"
          value={scope}
          onChange={setScope}
          options={[
            { label: '本项目', value: 'own' },
            { label: '全平台共享（可取用）', value: 'shared' },
          ]}
        />
        <Text type="secondary" style={{ fontSize: 12 }}>
          {scope === 'own' ? '本项目上传的 Skill，可编辑 / 回滚 / 删除' : '其它项目共享出来的，可直接下载取用'}
        </Text>
      </Space>

      <Table
        size="small"
        rowKey="id"
        loading={loading}
        columns={columns}
        dataSource={rows}
        pagination={false}
        locale={{
          emptyText: (
            <Empty
              image={Empty.PRESENTED_IMAGE_SIMPLE}
              description={
                <span style={{ fontSize: 12, color: '#86909c' }}>
                  {scope === 'own'
                    ? '本项目还没有 Skill —— 点右上「添加 Skill」粘贴内容，或在 Claude Code 里说「把 xxx 传到 testBench」'
                    : '还没有项目共享 Skill 出来'}
                </span>
              }
            />
          ),
        }}
      />

      {/* 添加 —— 粘贴正文（默认，最省事）/ 传压缩包（带附属文件时才需要） */}
      <Modal
        title="添加 Skill"
        open={addOpen}
        onCancel={closeAdd}
        width={720}
        footer={addTab === 'paste' ? [
          <Button key="cancel" onClick={closeAdd}>取消</Button>,
          <Button key="ok" type="primary" icon={<SaveOutlined />} loading={creating} onClick={handleCreateFromPaste}>
            保存
          </Button>,
        ] : null}
      >
        <Segmented
          block
          value={addTab}
          onChange={setAddTab}
          style={{ marginBottom: 14 }}
          options={[
            { label: '粘贴 SKILL.md（推荐）', value: 'paste' },
            { label: '上传压缩包', value: 'upload' },
          ]}
        />

        {addTab === 'paste' ? (
          <>
            <Alert
              type="info" showIcon style={{ marginBottom: 12 }}
              message={
                <span style={{ fontSize: 12 }}>
                  单文件 skill 直接把 SKILL.md 内容粘进来就行，不用打包。
                  <b>名字从 frontmatter 的 <Text code>name</Text> 读</b>，所以那一行必须写。
                </span>
              }
            />
            <Space style={{ marginBottom: 10 }}>
              <Text style={{ fontSize: 13 }}>可见性：</Text>
              <Segmented
                size="small"
                value={pasteVisibility}
                onChange={setPasteVisibility}
                options={[
                  { label: '全平台可取用', value: 'public' },
                  { label: '仅本项目', value: 'project' },
                ]}
              />
              <Button size="small" type="link" onClick={() => setPasteContent(SKILL_TEMPLATE)}>
                填入模板
              </Button>
            </Space>
            <Input.TextArea
              value={pasteContent}
              onChange={e => setPasteContent(e.target.value)}
              rows={20}
              placeholder={SKILL_TEMPLATE}
              style={{ fontFamily: 'var(--font-mono)', fontSize: 13, lineHeight: 1.6 }}
            />
            <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 8, marginBottom: 0 }}>
              同名会覆盖，覆盖前自动留档，可在版本历史里回滚。
            </Paragraph>
          </>
        ) : (
          <>
            <Alert
              type="info" showIcon style={{ marginBottom: 12 }}
              message={<span style={{ fontSize: 12 }}>只有 skill 目录里还有 references/、脚本等附属文件时才需要走这条路。</span>}
            />
            <Upload.Dragger
              accept=".zip,.tar.gz,.tgz"
              showUploadList={false}
              beforeUpload={handleUpload}
              disabled={uploading}
              style={{ padding: '28px 0' }}
            >
              {uploading ? <Spin tip="正在上传..." /> : (
                <>
                  <p><InboxOutlined style={{ fontSize: 40, color: '#7c5cff' }} /></p>
                  <p style={{ fontSize: 14, color: '#1d2129', marginTop: 8 }}>点击或拖拽上传 Skill 压缩包</p>
                  <p style={{ fontSize: 12, color: '#86909c' }}>
                    支持 .zip / .tar.gz，把整个 <Text code>skill-name/</Text> 目录打包<br />
                    包内必须有 SKILL.md，references/ 等附属文件会一起存
                  </p>
                </>
              )}
            </Upload.Dragger>
            <Paragraph type="secondary" style={{ fontSize: 12, marginTop: 12, marginBottom: 0 }}>
              打包命令：<Text code>tar czf feature-verify.tar.gz feature-verify</Text>
            </Paragraph>
          </>
        )}
      </Modal>

      {/* 编辑 */}
      <Drawer
        title={<Space><EditOutlined /> 编辑 Skill <Text code>{editing?.name}</Text></Space>}
        open={!!editing}
        onClose={() => setEditing(null)}
        width={760}
        footer={
          <div style={{ textAlign: 'right' }}>
            <Button onClick={() => setEditing(null)} style={{ marginRight: 8 }}>取消</Button>
            <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>保存</Button>
          </div>
        }
      >
        <Alert
          type="info" showIcon style={{ marginBottom: 12 }}
          message="保存后旧版本自动留档，可随时回滚。附属文件不在这里编辑 —— 要改就重新打包上传。"
        />
        <Space style={{ marginBottom: 12 }}>
          <Text style={{ fontSize: 13 }}>可见性：</Text>
          <Segmented
            size="small"
            value={editing?.visibility}
            onChange={v => setEditing(s => ({ ...s, visibility: v }))}
            options={[
              { label: '全平台可取用', value: 'public' },
              { label: '仅本项目', value: 'project' },
            ]}
          />
        </Space>
        <Input.TextArea
          value={editing?.content || ''}
          onChange={e => setEditing(s => ({ ...s, content: e.target.value }))}
          rows={26}
          style={{ fontFamily: 'var(--font-mono)', fontSize: 13, lineHeight: 1.6 }}
        />
      </Drawer>

      {/* 版本历史 */}
      <Modal
        title={<Space><HistoryOutlined /> 版本历史 <Text code>{versions?.name}</Text></Space>}
        open={!!versions}
        onCancel={() => setVersions(null)}
        footer={null}
        width={560}
      >
        {versions?.list?.length ? (
          <Table
            size="small" rowKey="version" pagination={false}
            dataSource={versions.list}
            columns={[
              { title: '版本', dataIndex: 'version', width: 70, render: v => <Tag>v{v}</Tag> },
              { title: '留档原因', dataIndex: 'note', render: n => <Text style={{ fontSize: 12 }}>{n}</Text> },
              {
                title: '时间', dataIndex: 'createdAt', width: 150,
                render: t => <Text type="secondary" style={{ fontSize: 12 }}>{t ? new Date(t).toLocaleString('zh-CN') : '-'}</Text>,
              },
              {
                title: '', width: 90,
                render: (_, r) => (
                  <Popconfirm
                    title={`回滚到 v${r.version}？`}
                    description="当前内容也会先留档"
                    okText="回滚" cancelText="取消"
                    onConfirm={() => handleRollback(versions.name, r.version)}
                  >
                    <Button size="small" icon={<RollbackOutlined />}>回滚</Button>
                  </Popconfirm>
                ),
              },
            ]}
          />
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有历史版本（当前是第一版）" />
        )}
      </Modal>

      {/* 怎么用 */}
      <Modal
        title="怎么把 Skill 传上来 / 取下去"
        open={howToOpen}
        onCancel={() => setHowToOpen(false)}
        footer={<Button type="primary" onClick={() => setHowToOpen(false)}>知道了</Button>}
        width={640}
      >
        <Paragraph style={{ fontSize: 13, marginBottom: 8 }}>
          <b>最省事：页面上粘贴</b> —— 单文件 skill 点「添加 Skill」，把 SKILL.md 内容复制进去保存，
          名字自动从 frontmatter 的 <Text code>name</Text> 读。什么都不用装、不用打包。
        </Paragraph>
        <Paragraph style={{ fontSize: 13, marginTop: 12, marginBottom: 8 }}>
          <b>批量 / 带附属文件：走 MCP</b> —— 项目侧的 Claude Code 已经连了 testBench MCP，
          直接说人话，它自己读文件自己推：
        </Paragraph>
        <pre style={{
          background: 'rgba(0,0,0,0.03)', padding: 12, borderRadius: 8,
          fontSize: 12, lineHeight: 1.8, overflowX: 'auto',
        }}>{MCP_SNIPPET}</pre>
        <Paragraph style={{ fontSize: 13, marginTop: 12, marginBottom: 8 }}>
          <b>或者：打包上传 / 下载</b>
        </Paragraph>
        <pre style={{
          background: 'rgba(0,0,0,0.03)', padding: 12, borderRadius: 8,
          fontSize: 12, lineHeight: 1.8, overflowX: 'auto',
        }}>{`# 传上来
tar czf feature-verify.tar.gz feature-verify
# 然后点「上传 Skill」拖进去

# 取下去（下载后解包到 .claude/skills/）
tar xzf feature-verify.tar.gz -C .claude/skills/`}</pre>
        <Alert
          type="warning" showIcon style={{ marginTop: 12 }}
          message="取到本地后要重启 Claude Code 会话，新 skill 才会被识别。"
        />
      </Modal>
    </Card>
  )
}
