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
    name: 'lum-case-generate',
    title: 'AI 用例生成',
    icon: <FileTextOutlined style={{ fontSize: 20, color: '#c9cdd4' }} />,
    // 页面入口 2026-08-19 下线：建的是 testforge task JSON,真正生成用例的是
    // CC 侧 /tf-forge（它自己就能读接口树）。这条 skill 文件还在,但已经没有
    // 按钮能触发它 —— 之前这里标"可用"还带编辑按钮,跟事实反了。
    status: 'retired',
    description: '从 API 接口定义和业务规则出发，自动生成覆盖 6 个维度的测试用例',
    input: '接口信息（选择或手动输入） + 业务规则 + 目标模块',
    output: '测试用例（标题 + 手动步骤 + 预期结果），自动入库',
    where: '入口 2026-08-19 下线，改由外部 Claude Code 的 /tf-forge 生成后回推',
    dimensions: ['正向流程', '参数验证', '业务规则', '边界值', '异常场景', '安全'],
    steps: [
      '收集上下文 — 读取项目 API 接口定义 + 查询已有用例（去重）',
      '维度规划 — AI 规划 6-10 个测试维度和每个维度的用例数',
      'AI 生成 — 按维度逐一生成用例，实时流式输出',
      '解析入库 — 解析 AI 输出，自动去重后写入用例管理',
    ],
    mcpTools: ['lum_list_api_tree', 'lum_list_cases', 'lum_get_folder_tree', 'lum_create_case'],
  },
  {
    name: 'lum-script-generate',
    title: 'AI 脚本生成',
    icon: <CodeOutlined style={{ fontSize: 20, color: '#c9cdd4' }} />,
    // 入口（AIScriptModal）已删除，而且这条从来没有对应的 skill 文件——
    // 之前标"可用"还带编辑按钮，点了会 404（GET /skills/lum-script-generate 查无此文件）。
    status: 'retired',
    description: '根据已有测试用例，自动生成 pytest + httpx 可执行的自动化测试脚本',
    input: '选中的测试用例（勾选一条或多条）',
    output: 'pytest 测试脚本代码',
    where: '入口（AIScriptModal）已删除，无对应 skill 文件',
    steps: [
      '读取用例 — 获取选中用例的步骤、前置条件、预期结果',
      'AI 生成 — 将用例转化为 pytest + httpx 代码',
      '输出脚本 — 展示代码，可复制到项目中直接运行',
    ],
    mcpTools: [],
  },
  {
    name: 'lum-quality-review',
    title: '质量评审（AI 审核）',
    icon: <SearchOutlined style={{ fontSize: 20, color: '#0ea5a0' }} />,
    // 这条早就是"可用"了，标"规划中 Phase 2"是最反的一条——它是全平台唯一
    // 被真实高频调用的能力（9 次调用，最近一次 08-18）。用例管理页的
    // 「AI 审核」按钮点的就是它。
    status: 'available',
    description: '按六维逐条评审用例质量：场景合理性/验证点到位/接口必要性/UI脚本/覆盖遗漏/纪律',
    input: '一条或若干条用例 + 对应的 API 接口定义',
    output: '六维评分 + 问题清单（致命/重要/次要）+ 结论落库到审核标签',
    where: '用例管理「AI 审核」按钮 / 用例详情「审核」页 / MCP lum_review_case',
    mcpTools: ['lum_list_cases', 'lum_get_case', 'lum_list_api_tree'],
  },
  {
    name: 'lum-explore',
    title: '探索测试',
    icon: <BugOutlined style={{ fontSize: 20, color: '#c9cdd4' }} />,
    status: 'retired',
    description: '已下线。它生成的章程唯一的输入是接口库，而全库 7 个 endpoint 的 url '
      + '一个都没填 —— 模型实际拿到的上下文就是模块名那几个字，出来的检查点'
      + '（"必填校验/边界值/权限隔离/SQL注入"）换个系统照样成立，跟被测系统没有关系。'
      + '章程之后也没有任何执行：勾检查点、写发现全靠人手填，3 个会话 0 个检查点被勾过。'
      + '探索归外部 Claude Code：它真能在页面上点一遍，'
      + '把探到的可操作项喂 lum_module_checkup(observed_actions=…) 跟现有用例对账。',
    input: '—',
    output: '—',
    where: '入口 2026-08-27 下线（原「探索测试」→「AI 生成章程」）',
    mcpTools: ['lum_module_checkup', 'lum_proxy_capture'],
  },
  {
    name: 'lum-diagnose',
    title: '失败诊断',
    icon: <FileSearchOutlined style={{ fontSize: 20, color: '#c9cdd4' }} />,
    status: 'retired',
    description: '已下线。失败归因改由外部 Claude Code 做（lum_get_failed_scenarios 拿现象和证据 → '
      + 'lum_submit_analysis 提归因），平台只按规则算"现象"、由人确认"原因"。'
      + '平台自己再诊断一份，等于在同一件事上给出第四个声音。',
    input: '—',
    output: '—',
    where: '入口从未存在过（页面上写的那个「AI 诊断」按钮是不存在的）',
    mcpTools: ['lum_get_failed_scenarios', 'lum_submit_analysis'],
  },
  {
    name: 'lum-doc-generate',
    title: '文档生成',
    icon: <BookOutlined style={{ fontSize: 20, color: '#c9cdd4' }} />,
    // 「文档管理」模块 2026-08-27 整体下线（docs/cc-platform-loop-spec.md §14）。
    // 页面、路由、后端 /api/projects/{id}/documents/*、doc_generator.py、
    // 这条 SKILL.md、MCP 的 lum_get_doc_spec 一并删了 —— 所以这里必须标
    // retired：标"可用"会露出一个点了就 404 的编辑按钮（跟 lum-script-generate
    // 当初一模一样的坑）。
    status: 'retired',
    description: '已下线。平台侧驱动浏览器截图 + AI 写文档这条路做得不好：'
      + '截图靠通用启发式点菜单，认不准就截一堆列表页；文字是 AI 看图编的，'
      + '没有需求做对照。要文档就在 Claude Code 里自己实操系统写，那边有真浏览器。',
    input: '—',
    output: '—',
    where: '入口 2026-08-27 下线（原「文档管理」→ 生成按钮）',
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
            <b>Skill</b> 是 Lumiere 平台的 AI 工作流定义（YAML + Markdown 文件），包含：<br/>
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
              // 第四态 'inline'（已上线但没有独立 skill 文件）2026-08-27 随 lum-explore
              // 一起收掉了 —— 它只为那一张卡存在。真再出现"功能活着但没 skill 文件"
              // 的情况，照着 git 历史加回来，别为了省事标成 available（编辑按钮会 404）。
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
