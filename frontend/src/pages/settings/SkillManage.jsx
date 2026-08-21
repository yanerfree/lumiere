import { useState } from 'react'
import { Card, Tag, Space, Typography, Alert, Steps, Collapse, Button, Drawer, Input, message } from 'antd'
import {
  ThunderboltOutlined, FileTextOutlined, CodeOutlined, SearchOutlined,
  BugOutlined, FileSearchOutlined, BookOutlined, CheckCircleOutlined,
  ClockCircleOutlined, RobotOutlined, ApiOutlined, EditOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import { api } from '../../utils/request'
import ProjectSkillSection from './ProjectSkillSection'

const { Text, Paragraph } = Typography

const SKILLS = [
  {
    name: 'tb-case-generate',
    title: 'AI 用例生成',
    icon: <FileTextOutlined style={{ fontSize: 20, color: '#0ea5a0' }} />,
    status: 'available',
    description: '从 API 接口定义和业务规则出发，自动生成覆盖 6 个维度的测试用例',
    input: '接口信息（选择或手动输入） + 业务规则 + 目标模块',
    output: '测试用例（标题 + 手动步骤 + 预期结果），自动入库',
    where: '用例管理页 → 工具栏「AI 生成用例」按钮',
    dimensions: ['正向流程', '参数验证', '业务规则', '边界值', '异常场景', '安全'],
    steps: [
      '收集上下文 — 读取项目 API 接口定义 + 查询已有用例（去重）',
      '维度规划 — AI 规划 6-10 个测试维度和每个维度的用例数',
      'AI 生成 — 按维度逐一生成用例，实时流式输出',
      '解析入库 — 解析 AI 输出，自动去重后写入用例管理',
    ],
    mcpTools: ['tb_list_api_tree', 'tb_list_cases', 'tb_get_folder_tree', 'tb_create_case'],
  },
  {
    name: 'tb-script-generate',
    title: 'AI 脚本生成',
    icon: <CodeOutlined style={{ fontSize: 20, color: '#0ea5a0' }} />,
    status: 'available',
    description: '根据已有测试用例，自动生成 pytest + httpx 可执行的自动化测试脚本',
    input: '选中的测试用例（勾选一条或多条）',
    output: 'pytest 测试脚本代码',
    where: '用例管理页 → 勾选用例 → 工具栏「AI 生成脚本」按钮',
    steps: [
      '读取用例 — 获取选中用例的步骤、前置条件、预期结果',
      'AI 生成 — 将用例转化为 pytest + httpx 代码',
      '输出脚本 — 展示代码，可复制到项目中直接运行',
    ],
    mcpTools: [],
  },
  {
    name: 'tb-quality-review',
    title: '质量评审',
    icon: <SearchOutlined style={{ fontSize: 20, color: '#c9cdd4' }} />,
    status: 'planned',
    phase: 'Phase 2',
    description: 'AI 从完整性、准确性、有效性、可执行性 4 个维度评审用例质量',
    input: '一个模块下的所有用例 + 对应的 API 接口定义',
    output: '质量评分（0-100）+ 问题清单 + 覆盖矩阵 + 改进建议',
    where: '计划入口：用例管理 → 选模块 → AI 评审',
    mcpTools: ['tb_list_cases', 'tb_get_case', 'tb_list_api_tree'],
  },
  {
    name: 'tb-explore',
    title: '探索测试',
    icon: <BugOutlined style={{ fontSize: 20, color: '#c9cdd4' }} />,
    status: 'planned',
    phase: 'Phase 2',
    description: 'AI 辅助人工探索测试：生成章程 → 引导逐项检查 → 记录发现 → 输出报告',
    input: '目标模块 + API 接口 + 已有用例覆盖情况',
    output: '探索测试报告（Bug / 风险 / 改进建议 + 覆盖热力图）',
    where: '计划入口：项目菜单新增「探索测试」',
    mcpTools: ['tb_list_api_tree', 'tb_list_cases'],
  },
  {
    name: 'tb-diagnose',
    title: '失败诊断',
    icon: <FileSearchOutlined style={{ fontSize: 20, color: '#c9cdd4' }} />,
    status: 'retired',
    description: '已下线。失败归因改由外部 Claude Code 做（tb_get_failed_scenarios 拿现象和证据 → '
      + 'tb_submit_analysis 提归因），平台只按规则算"现象"、由人确认"原因"。'
      + '平台自己再诊断一份，等于在同一件事上给出第四个声音。',
    input: '—',
    output: '—',
    where: '入口从未存在过（页面上写的那个「AI 诊断」按钮是不存在的）',
    mcpTools: ['tb_get_failed_scenarios', 'tb_submit_analysis'],
  },
  {
    name: 'tb-doc-generate',
    title: '文档生成',
    icon: <BookOutlined style={{ fontSize: 20, color: '#0ea5a0' }} />,
    status: 'available',
    description: '自动操作系统截图 + AI 写文档，支持平台直接生成和 Claude Code 两种方式',
    input: '被测系统地址 + 账号密码 + 文档范围 + 目标读者',
    output: '带截图的 Markdown 操作手册，可导出 HTML / ZIP',
    where: '项目菜单「文档管理」→ 生成按钮',
    mcpTools: [],
  },
]

export default function SkillManage() {
  const [editSkill, setEditSkill] = useState(null)
  const [editContent, setEditContent] = useState('')
  const [saving, setSaving] = useState(false)

  const handleEdit = async (skillName) => {
    try {
      const res = await api.get(`/skills/${skillName}`)
      setEditSkill(skillName)
      setEditContent(res.data.content)
    } catch { message.error('加载失败') }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await api.put(`/skills/${editSkill}`, { content: editContent })
      message.success('Skill 已保存')
      setEditSkill(null)
    } catch { message.error('保存失败') }
    finally { setSaving(false) }
  }

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: '#1d2129' }}>
          <ThunderboltOutlined style={{ marginRight: 8 }} />
          Skill 管理
        </h2>
        <span style={{ fontSize: 13, color: '#86909c' }}>
          Skill 定义 AI 的行为 — 做什么、怎么做、调用哪些工具、输出什么。本页管两类：
          上半是<b>平台 Skill</b>（后端执行、绑模型档位），下半是<b>项目 Skill</b>（Claude Code 侧执行，可上传共享给其它项目）。
        </span>
      </div>

      <Alert
        type="info"
        showIcon
        closable
        message="Skill 是什么？"
        description={
          <div style={{ fontSize: 12, lineHeight: 2 }}>
            <b>Skill</b> 是 testBench 平台的 AI 工作流定义（YAML + Markdown 文件），包含：<br/>
            <b>步骤</b> — AI 按步骤执行（收集上下文 → 生成 → 入库），每步有明确的输入输出<br/>
            <b>工具</b> — Skill 执行时调用的 MCP 工具（读取接口定义、创建用例等）<br/>
            <b>质量红线</b> — 约束 AI 输出的质量规则（如 P0 不超过 15%、每条用例一个验证点）<br/>
            Web 引擎在后端自动执行 Skill；Claude Code 用户可在终端手动调用。
          </div>
        }
        style={{ marginBottom: 16 }}
      />

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {SKILLS.map(skill => (
          <Card
            key={skill.name}
            size="small"
            style={{
              borderLeft: skill.status === 'available' ? '3px solid #0ea5a0' : '3px solid rgba(0,0,0,0.15)',
            }}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
              <Space size="middle">
                {skill.icon}
                <div>
                  <Space>
                    <Text strong style={{ fontSize: 16 }}>{skill.title}</Text>
                    <Text code style={{ fontSize: 12 }}>{skill.name}</Text>
                  </Space>
                  <div><Text type="secondary" style={{ fontSize: 13 }}>{skill.description}</Text></div>
                </div>
              </Space>
              <Space>
                {skill.status === 'available' && (
                  <Button size="small" icon={<EditOutlined />} onClick={() => handleEdit(skill.name)}>编辑</Button>
                )}
                {skill.status === 'available'
                  ? <Tag color="cyan" icon={<CheckCircleOutlined />}>可用</Tag>
                  : skill.status === 'retired'
                    ? <Tag color="default">已下线</Tag>
                    : <Tag icon={<ClockCircleOutlined />}>{skill.phase} 规划中</Tag>
                }
              </Space>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px 24px', fontSize: 13, lineHeight: 1.8 }}>
              <div><Text strong>输入：</Text>{skill.input}</div>
              <div><Text strong>输出：</Text>{skill.output}</div>
              <div><Text strong>入口：</Text>{skill.where}</div>
              <div>
                <Text strong>MCP 工具：</Text>
                {skill.mcpTools.length > 0
                  ? skill.mcpTools.map(t => <Tag key={t} style={{ fontSize: 11 }}>{t}</Tag>)
                  : <Text type="secondary">不依赖 MCP</Text>
                }
              </div>
            </div>

            {skill.steps && (
              <div style={{ marginTop: 12 }}>
                <Collapse
                  size="small"
                  items={[{
                    key: '1',
                    label: <Text strong style={{ fontSize: 13 }}>执行步骤（{skill.steps.length} 步）</Text>,
                    children: (
                      <Steps
                        direction="vertical"
                        size="small"
                        current={-1}
                        items={skill.steps.map((s, i) => ({
                          title: s.split(' — ')[0],
                          description: s.split(' — ')[1] || '',
                        }))}
                        style={{ marginTop: 4 }}
                      />
                    ),
                  }]}
                />
              </div>
            )}

            {skill.dimensions && (
              <div style={{ marginTop: 8 }}>
                <Text strong style={{ fontSize: 13 }}>覆盖维度：</Text>
                <Space size={4} style={{ marginLeft: 8 }}>
                  {skill.dimensions.map(d => <Tag key={d} color="#0ea5a0">{d}</Tag>)}
                </Space>
              </div>
            )}
          </Card>
        ))}
      </div>

      <ProjectSkillSection />

      <Drawer
        title={<Space><EditOutlined /> 编辑 Skill <Text code>{editSkill}</Text></Space>}
        open={!!editSkill}
        onClose={() => setEditSkill(null)}
        width={700}
        footer={
          <div style={{ textAlign: 'right' }}>
            <Button onClick={() => setEditSkill(null)} style={{ marginRight: 8 }}>取消</Button>
            <Button type="primary" icon={<SaveOutlined />} loading={saving} onClick={handleSave}>保存</Button>
          </div>
        }
      >
        <Alert type="info" showIcon closable style={{ marginBottom: 12 }}
          message="编辑 SKILL.md 文件内容。修改后会立即生效，下次执行 Skill 时使用新版本。" />
        <Input.TextArea
          value={editContent}
          onChange={e => setEditContent(e.target.value)}
          rows={28}
          style={{ fontFamily: 'var(--font-mono)', fontSize: 13, lineHeight: 1.6 }}
        />
      </Drawer>
    </div>
  )
}
