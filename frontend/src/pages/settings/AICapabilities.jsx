import { Card, Tag, Space, Typography, Divider, Table, Timeline, Badge } from 'antd'
import {
  RobotOutlined, ApiOutlined, ThunderboltOutlined, FileTextOutlined,
  BugOutlined, SearchOutlined, CodeOutlined, FileSearchOutlined,
  CheckCircleOutlined, ClockCircleOutlined, ToolOutlined,
  ExperimentOutlined, BookOutlined,
} from '@ant-design/icons'

const { Text, Paragraph } = Typography

const PHASES = [
  {
    phase: 'Phase 1',
    title: '基础能力',
    tag: '已完成',
    tagColor: 'success',
    items: [
      {
        title: 'AI 用例生成',
        icon: <FileTextOutlined />,
        status: 'done',
        what: '从 API 接口定义 + 业务规则，自动生成多维度测试用例',
        where: '用例管理 → 工具栏「AI 生成用例」',
        output: '测试用例（手动步骤），6 维度覆盖，自动去重入库',
        dimensions: '正向流程 · 参数验证 · 业务规则 · 边界值 · 异常场景 · 安全',
      },
      {
        title: 'AI 脚本生成',
        icon: <CodeOutlined />,
        status: 'done',
        what: '根据已有用例生成 pytest + httpx 自动化测试脚本',
        where: '用例管理 → 勾选用例 → 工具栏「AI 生成脚本」',
        output: 'pytest 脚本代码，可直接复制到项目中运行',
      },
      {
        title: 'MCP Server',
        icon: <ApiOutlined />,
        status: 'done',
        what: '暴露平台数据的标准 MCP 协议接口，供 AI 工具读写',
        where: `MCP 地址: http://${window.location.hostname}:18800/mcp/`,
        output: '8 个工具：用例 CRUD、API 接口查询、环境变量',
      },
      {
        title: 'AI 配置管理',
        icon: <ToolOutlined />,
        status: 'done',
        what: '多级 AI 配置：系统级创建 → 分配给项目 → 项目级选择或自建',
        where: '系统菜单「AI 管理」/ 项目菜单「AI 智能 → AI 配置」',
        output: '每个项目独立使用自己的 AI 配置，互不影响',
      },
    ],
  },
  {
    phase: 'Phase 2',
    title: '核心 Skill',
    tag: '进行中',
    tagColor: 'processing',
    items: [
      {
        title: '质量评审',
        icon: <SearchOutlined />,
        status: 'done',
        what: 'AI 从完整性、准确性、有效性、可执行性 4 维度评审用例质量并打分',
        where: '用例管理 → 工具栏「AI 评审」按钮',
        output: '质量分（0-100）+ 问题清单 + 覆盖矩阵 + 改进建议',
      },
      {
        title: '探索测试',
        icon: <BugOutlined />,
        status: 'planned',
        what: 'AI 辅助人工探索测试：生成章程 → 引导检查 → 记录发现 → 输出报告',
        where: '计划入口：项目菜单新增「探索测试」',
        output: '探索报告（Bug / 风险 / 改进 + 覆盖热力图）',
      },
      {
        title: '失败诊断',
        icon: <FileSearchOutlined />,
        status: 'done',
        what: '分析测试失败原因，3 分类（脚本Bug / 系统Bug / 环境问题）+ 修复建议',
        where: '测试报告 → 失败用例旁「AI 诊断」按钮',
        output: '诊断结论 + 置信度 + 可行动的修复方案',
      },
    ],
  },
  {
    phase: 'Phase 3',
    title: '文档能力',
    tag: '已完成',
    tagColor: 'success',
    items: [
      {
        title: '文档生成',
        icon: <BookOutlined />,
        status: 'done',
        what: '自动截图 + AI 写文档。平台直接生成或通过 Claude Code 生成',
        where: '项目菜单「文档管理」→ 生成按钮',
        output: '带截图的 Markdown 文档，可导出 HTML/ZIP',
      },
    ],
  },
  {
    phase: 'Phase 4',
    title: '闭环运营',
    tag: '已完成',
    tagColor: 'success',
    items: [
      {
        title: '病历系统',
        icon: <ExperimentOutlined />,
        status: 'done',
        what: '每条用例的历史时间线：生成 → 评审 → 执行 → 诊断，自动标签',
        where: 'API: GET /api/cases/{id}/file',
        output: '事件时间线 + 自动标签（#不稳定 #需要关注 #待验证）',
      },
      {
        title: '用量统计',
        icon: <ToolOutlined />,
        status: 'done',
        what: 'Token 消耗追踪，按项目/Skill 统计用量',
        where: 'API: GET /api/projects/{id}/ai-usage',
        output: '按 Skill 分组的调用次数和 Token 消耗',
      },
    ],
  },
]

const MCP_TOOLS = [
  { name: 'tb_list_cases', description: '列出分支下的测试用例，支持分页和筛选', category: '用例' },
  { name: 'tb_get_case', description: '获取单条测试用例的完整详情', category: '用例' },
  { name: 'tb_create_case', description: '创建测试用例，自动生成编号和目录', category: '用例' },
  { name: 'tb_get_folder_tree', description: '获取用例文件夹树形结构', category: '用例' },
  { name: 'tb_list_api_tree', description: '获取项目所有 API 接口树', category: 'API' },
  { name: 'tb_get_api_node', description: '获取 API 节点详情（method/url/body）', category: 'API' },
  { name: 'tb_list_environments', description: '列出所有测试环境', category: '环境' },
  { name: 'tb_get_merged_variables', description: '获取合并后的环境变量', category: '环境' },
]

export default function AICapabilities() {
  const mcpColumns = [
    { title: '工具名称', dataIndex: 'name', width: 220, render: (n) => <Text code>{n}</Text> },
    { title: '分类', dataIndex: 'category', width: 80, render: (c) => <Tag>{c}</Tag> },
    { title: '说明', dataIndex: 'description' },
  ]

  return (
    <div>
      <div style={{ marginBottom: 16 }}>
        <h2 style={{ fontSize: 18, fontWeight: 600, margin: 0, color: '#1d2129' }}>
          <RobotOutlined style={{ marginRight: 8 }} />
          AI 能力总览
        </h2>
        <span style={{ fontSize: 13, color: '#86909c' }}>
          testBench 平台的 AI 能力路线图。已完成的功能可直接使用，规划中的功能按阶段推进。
        </span>
      </div>

      {/* 按阶段展示 */}
      {PHASES.map((phase) => (
        <div key={phase.phase} style={{ marginBottom: 24 }}>
          <Divider orientation="left" style={{ margin: '8px 0 12px' }}>
            <Space>
              <Text strong>{phase.phase}</Text>
              <Text type="secondary">{phase.title}</Text>
              <Tag color={phase.tagColor}>{phase.tag}</Tag>
            </Space>
          </Divider>

          <div style={{ display: 'grid', gridTemplateColumns: phase.items.length === 1 ? '1fr' : '1fr 1fr', gap: 12 }}>
            {phase.items.map(item => (
              <Card
                key={item.title}
                size="small"
                style={{ borderLeft: item.status === 'done' ? '3px solid #0ea5a0' : '3px solid rgba(0,0,0,0.15)' }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 6 }}>
                  <Space>
                    {item.icon}
                    <Text strong style={{ fontSize: 15 }}>{item.title}</Text>
                  </Space>
                  {item.status === 'done'
                    ? <Tag color="cyan" icon={<CheckCircleOutlined />}>可用</Tag>
                    : <Tag icon={<ClockCircleOutlined />}>规划中</Tag>
                  }
                </div>
                <div style={{ fontSize: 13, lineHeight: 1.9 }}>
                  <div><Text strong>做什么：</Text>{item.what}</div>
                  <div><Text strong>在哪用：</Text>{item.where}</div>
                  <div><Text strong>输出：</Text>{item.output}</div>
                  {item.dimensions && (
                    <div style={{ marginTop: 4 }}>
                      <Text strong>覆盖维度：</Text>
                      <span style={{ color: '#0ea5a0' }}>{item.dimensions}</span>
                    </div>
                  )}
                </div>
              </Card>
            ))}
          </div>
        </div>
      ))}

      {/* MCP 工具 */}
      <Divider orientation="left" style={{ margin: '8px 0 12px' }}>
        <Space><ApiOutlined /> MCP 工具（AI 读写平台数据的接口）</Space>
      </Divider>

      <Card size="small" style={{ marginBottom: 12, background: 'rgba(0,0,0,0.02)' }}>
        <div style={{ fontSize: 13, lineHeight: 2 }}>
          <b>MCP Server 地址：</b><Text code>{`http://${window.location.hostname}:18800/mcp/`}</Text>
          <br/>
          <b>用途：</b>AI Skill 执行时通过这些工具读取项目数据并写入生成结果。Claude Code 等外部 MCP 客户端也可连接使用。
        </div>
      </Card>

      <Table
        rowKey="name"
        columns={mcpColumns}
        dataSource={MCP_TOOLS}
        pagination={false}
        size="small"
        style={{ marginBottom: 24 }}
      />

      {/* 双引擎 */}
      <Divider orientation="left" style={{ margin: '8px 0 12px' }}>
        <Space><ToolOutlined /> 两种使用方式</Space>
      </Divider>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Card size="small" style={{ borderLeft: '3px solid #0ea5a0' }}>
          <Space style={{ marginBottom: 8 }}>
            <Text strong style={{ fontSize: 15 }}>Web 引擎（浏览器）</Text>
            <Tag color="#0ea5a0">推荐</Tag>
          </Space>
          <div style={{ fontSize: 13, lineHeight: 2 }}>
            <div>直接在平台页面操作，零安装</div>
            <div><b>入口：</b>用例管理工具栏 AI 按钮</div>
            <div><b>特点：</b>实时进度 · 预览导入 · 可暂停</div>
          </div>
        </Card>
        <Card size="small" style={{ borderLeft: '3px solid rgba(0,0,0,0.15)' }}>
          <Space style={{ marginBottom: 8 }}>
            <Text strong style={{ fontSize: 15 }}>Claude Code 引擎（CLI）</Text>
            <Tag>高级</Tag>
          </Space>
          <div style={{ fontSize: 13, lineHeight: 2 }}>
            <div>终端通过 MCP 协议调用平台工具</div>
            <div><b>入口：</b>Claude Code → <Text code>/tf-forge</Text></div>
            <div><b>特点：</b>更强 LLM · 读本地代码 · 灵活定制</div>
          </div>
        </Card>
      </div>
    </div>
  )
}
