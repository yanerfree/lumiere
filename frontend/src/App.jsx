import { useState, useEffect } from 'react'
import { Routes, Route, useNavigate, useLocation, Navigate, useParams } from 'react-router-dom'
import { Layout, Menu, Avatar, Dropdown, Button, Tooltip, message, Modal, Form, Input } from 'antd'
import {
  FolderOutlined, FileTextOutlined, UnorderedListOutlined, BarChartOutlined,
  SettingOutlined, UserOutlined, FileSearchOutlined, ApiOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, BellOutlined, RobotOutlined,
  ThunderboltOutlined, BugOutlined, ToolOutlined, SendOutlined,
  NodeIndexOutlined, SearchOutlined,
  GlobalOutlined, SafetyCertificateOutlined, DatabaseOutlined, TranslationOutlined,
  DeploymentUnitOutlined,
} from '@ant-design/icons'
import { api } from './utils/request'
import { useLang } from './utils/i18n.jsx'
import BranchSelector from './components/BranchSelector'
import ServiceStatusBadge from './components/ServiceStatusBadge'
import ProjectList from './pages/projects/ProjectList'
import CaseManagement from './pages/cases/CaseManagement'
import ReviewReport from './pages/cases/ReviewReport'
import CaseDetail from './pages/cases/CaseDetail'
import PlanList from './pages/plan/PlanList'
import PlanDetail from './pages/plan/PlanDetail'
import ReportList from './pages/report/ReportList'
import ReportDetail from './pages/report/ReportDetail'
import Login from './pages/auth/Login'
import ManualRecord from './pages/plan/ManualRecord'
import EnvConfig from './pages/settings/EnvConfig'
import UserManagement from './pages/settings/UserManagement'
import AuditLogs from './pages/settings/AuditLogs'
import ChannelConfig from './pages/settings/ChannelConfig'
import ApiManagement from './pages/apis/ApiManagement'
import LlmMock from './pages/llm-mock/LlmMock'
import ApiMock from './pages/api-mock/ApiMock'
import ProxyProbe from './pages/proxy-probe/ProxyProbe'
import McpMock from './pages/mcp-mock/McpMock'
import OAuth2Mock from './pages/oauth2-mock/OAuth2Mock'
import Toolbox from './pages/toolbox/Toolbox'
import HttpClient from './pages/http-client/HttpClient'
import LoadTest from './pages/load-test/LoadTest'
import AIProviderConfig from './pages/settings/AIProviderConfig'
import ProjectAIConfig from './pages/settings/ProjectAIConfig'
import AutomationData from './pages/settings/AutomationData'
import I18nMessages from './pages/settings/I18nMessages'
import AICapabilities from './pages/settings/AICapabilities'
import SkillManage from './pages/settings/SkillManage'
import MCPTools from './pages/settings/MCPTools'
import Exploratory from './pages/exploratory/Exploratory'
import Documents from './pages/documents/Documents'
import SystemServices from './pages/settings/SystemServices'

const { Header, Sider, Content } = Layout

// 「接口测试」模块 2026-08-15 下线后，老书签的落点。
// 不能写成 <Navigate to="../cases">：这些路由挂在 AppLayout 内层的第二个 <Routes> 里，
// 相对路径的基准不是 /projects/:projectId，实测会跳到 /cases（丢掉项目前缀）—— 照样白屏。
function RedirectToCases() {
  const { projectId } = useParams()
  return <Navigate to={`/projects/${projectId}/cases`} replace />
}

function RequireAuth({ children }) {
  const token = localStorage.getItem('token')
  if (!token) return <Navigate to="/login" replace />
  return children
}

function AppLayout() {
  const [collapsed, setCollapsed] = useState(false)
  const [projectName, setProjectName] = useState('')
  const [pwdOpen, setPwdOpen] = useState(false)
  const [pwdLoading, setPwdLoading] = useState(false)
  const [pwdForm] = Form.useForm()
  const navigate = useNavigate()
  const location = useLocation()
  const { t, lang, setLang } = useLang()

  const user = JSON.parse(localStorage.getItem('user') || '{}')

  // 从 URL 提取当前 projectId，判断是否在项目内
  const projectMatch = location.pathname.match(/\/projects\/([^/]+)/)
  const projectId = projectMatch ? projectMatch[1] : null
  const isProjectPage = !!projectId

  // 进入项目时获取项目名称
  useEffect(() => {
    if (!projectId) { setProjectName(''); return }
    api.get('/projects').then(res => {
      const p = res.data.find(item => item.id === projectId)
      setProjectName(p ? p.name : '')
    }).catch(() => {})
  }, [projectId])

  // 侧边栏分组。按**这个功能实际作用在什么上**分，不按菜单名字听起来像什么。
  //
  // 上一版按名字分，错了两处：
  // - Mock 服务单开一档，跟「测试工具」平级。可 Mock 本来就是测试工具的一种
  //   （造一个可控的上游好让被测系统跑起来），拆开只是因为它名字里没有"工具"两个字。
  // - 环境配置 / 通知渠道 / AI 服务配置塞进「系统设置」。它们听起来像平台配置，
  //   实际全是**项目跑测试要用的资源**：环境 = 跑在哪，通知渠道 = 结果发给谁，
  //   AI 服务 = AI 功能用哪个模型（`ai_provider_configs.assigned_project_ids`
  //   明摆着是按项目分配的）。真正"平台自己的"只有用户、服务端口、审计日志三样。
  //
  // 另外全部改回**可折叠的子菜单**：上一版用 group 是为了消灭「只有一个子项的伪二级」，
  // 但顺手把"能收起来"也消灭了 —— 十几条平铺出来没法收，比多一层壳更烦。
  // 现在没有一个子菜单只挂一项，折叠就纯是收益。展开状态存 localStorage，
  // 否则每次跳页都弹回默认，收起来等于没收。
  const menuItems = isProjectPage ? [
    { key: '/projects', icon: <FolderOutlined />, label: t('menu.back') },
    { type: 'divider' },
    {
      key: 'g-design', icon: <FileTextOutlined />, label: t('menu.group.design'),
      children: [
        { key: `/projects/${projectId}/cases`, icon: <FileTextOutlined />, label: t('menu.cases') },
        { key: `/projects/${projectId}/apis`, icon: <ApiOutlined />, label: t('menu.apis') },
      ],
    },
    {
      key: 'g-exec', icon: <BarChartOutlined />, label: t('menu.group.exec'),
      children: [
        { key: `/projects/${projectId}/plans`, icon: <UnorderedListOutlined />, label: t('menu.plans') },
        { key: `/projects/${projectId}/reports`, icon: <BarChartOutlined />, label: t('menu.reports') },
        // 审核报告放在「执行与产出」下 —— 它是产出（哪些模块审过了、还缺哪类用例），
        // 不是设计期的东西。挂在用例导航的铅笔旁边太隐蔽，而它要能被跟进。
        { key: `/projects/${projectId}/review-report`, icon: <SearchOutlined />, label: t('menu.reviewReport') },
        { key: `/projects/${projectId}/exploratory`, icon: <BugOutlined />, label: t('menu.exploratory') },
        { key: `/projects/${projectId}/documents`, icon: <FileTextOutlined />, label: t('menu.documents') },
      ],
    },
    {
      key: 'g-ai', icon: <RobotOutlined />, label: t('menu.group.ai'),
      children: [
        { key: `/projects/${projectId}/settings/ai-capabilities`, icon: <ThunderboltOutlined />, label: t('menu.ai.capabilities') },
        { key: `/projects/${projectId}/settings/skills`, icon: <FileTextOutlined />, label: t('menu.ai.skills') },
        { key: `/projects/${projectId}/settings/mcp-tools`, icon: <ApiOutlined />, label: t('menu.ai.mcp') },
        { key: `/projects/${projectId}/settings/ai`, icon: <SettingOutlined />, label: t('menu.ai.config') },
      ],
    },
    {
      key: 'g-proj-config', icon: <SettingOutlined />, label: t('menu.group.projectConfig'),
      children: [
        { key: `/projects/${projectId}/settings/env`, icon: <GlobalOutlined />, label: t('menu.envConfig') },
        { key: `/projects/${projectId}/settings/automation-data`, icon: <DatabaseOutlined />, label: t('menu.automationData') },
        { key: `/projects/${projectId}/settings/i18n`, icon: <TranslationOutlined />, label: t('menu.i18nDict') },
        { key: `/projects/${projectId}/logs`, icon: <FileSearchOutlined />, label: t('menu.logs') },
      ],
    },
  ] : [
    // 这三档的划分和排序由用户指定（2026-08-17），别再按自己的理解重排。
    // 项目列表跟 AI 服务/通知渠道放一起，是因为它们都是**项目要用的**配置，
    // 不是平台自己的；平台自己的只有用户、日志、端口三样，归系统管理。
    //
    // 「环境配置」2026-08-21 从这里挪进了项目壳的「项目配置」档 —— 不是重排，
    // 是环境从全局改成了项目级（environments 表加了 project_id，
    // 见 docs/data-scoping-and-isolation.md §4），这里已经没有项目可依附。
    {
      key: 'g-project', icon: <FolderOutlined />, label: t('menu.group.project'),
      children: [
        { key: '/projects', icon: <FolderOutlined />, label: t('menu.projects') },
        { key: '/settings/ai-providers', icon: <RobotOutlined />, label: t('menu.aiProviders') },
        { key: '/settings/channels', icon: <BellOutlined />, label: t('menu.channels') },
      ],
    },
    {
      // Mock 也在这一档：造可控上游本来就是为了让被测系统跑起来，跟压测、抓包是一类事
      key: 'g-tools', icon: <ToolOutlined />, label: t('menu.group.tools'),
      children: [
        { key: '/tools/api-mock', icon: <GlobalOutlined />, label: t('menu.apiMock') },
        { key: '/tools/llm-mock', icon: <RobotOutlined />, label: t('menu.llmMock') },
        { key: '/tools/mcp-mock', icon: <ApiOutlined />, label: t('menu.mcpMock') },
        { key: '/tools/oauth2-mock', icon: <SafetyCertificateOutlined />, label: t('menu.oauth2Mock') },
        { key: '/tools/proxy-probe', icon: <NodeIndexOutlined />, label: t('menu.proxyProbe') },
        { key: '/tools/http-client', icon: <SendOutlined />, label: t('menu.httpClient') },
        { key: '/tools/toolbox', icon: <ToolOutlined />, label: t('menu.toolbox') },
        { key: '/tools/load-test', icon: <ThunderboltOutlined />, label: t('menu.loadTest') },
      ],
    },
    {
      key: 'g-system', icon: <DeploymentUnitOutlined />, label: t('menu.group.system'),
      children: [
        ...(user.role === 'admin' ? [
          { key: '/settings/users', icon: <UserOutlined />, label: t('menu.users') },
        ] : []),
        { key: '/settings/logs', icon: <FileSearchOutlined />, label: t('menu.logs') },
        { key: '/settings/services', icon: <DeploymentUnitOutlined />, label: t('menu.services') },
      ],
    },
  ]

  // 展开哪些一级菜单。存起来 —— 不存的话每跳一次页就弹回默认，"能收起"等于没有。
  const DEFAULT_OPEN = ['g-design', 'g-exec', 'g-ai', 'g-proj-config', 'g-project', 'g-tools', 'g-system']
  const [openKeys, setOpenKeys] = useState(() => {
    try {
      const saved = JSON.parse(localStorage.getItem('menuOpenKeys'))
      return Array.isArray(saved) ? saved : DEFAULT_OPEN
    } catch { return DEFAULT_OPEN }
  })
  const handleOpenChange = (keys) => {
    setOpenKeys(keys)
    localStorage.setItem('menuOpenKeys', JSON.stringify(keys))
  }

  const handleLogout = async () => {
    try { await api.post('/auth/logout') } catch { /* 忽略，重点是清本地 */ }
    localStorage.removeItem('token')
    localStorage.removeItem('refreshToken')
    localStorage.removeItem('user')
    message.success('已退出登录')
    navigate('/login', { replace: true })
  }

  const handleChangePassword = async () => {
    let values
    try { values = await pwdForm.validateFields() } catch { return }
    setPwdLoading(true)
    try {
      await api.post('/auth/change-password', { oldPassword: values.oldPassword, newPassword: values.newPassword })
      message.success('密码修改成功，请重新登录')
      setPwdOpen(false)
      pwdForm.resetFields()
      localStorage.removeItem('token')
      localStorage.removeItem('refreshToken')
      localStorage.removeItem('user')
      navigate('/login', { replace: true })
    } catch { /* request.js 已展示错误 */ } finally { setPwdLoading(false) }
  }

  const userMenu = {
    items: [
      { key: 'changePwd', label: '修改密码', onClick: () => { pwdForm.resetFields(); setPwdOpen(true) } },
      { type: 'divider' },
      { key: 'logout', label: '退出登录', onClick: handleLogout },
    ]
  }

  const displayName = user.username === 'admin' ? '管理员' : user.username || '用户'

  return (
    <Layout className="app-layout-root" style={{ minHeight: '100vh' }}>
      <style>{`
        .app-layout-root {
          position: relative;
        }
        /* 一级菜单收起后只剩图标，子项走 antd 的浮层，不需要额外处理 */
      `}</style>
      {/* 顶栏 */}
      <Header style={{
        background: 'rgba(255,255,255,0.35)', height: 46, lineHeight: '46px', padding: '0 16px',
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        borderBottom: '1px solid rgba(0,0,0,0.04)',
        backdropFilter: 'blur(12px)', WebkitBackdropFilter: 'blur(12px)',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <img src="/favicon.svg" alt="" style={{ width: 26, height: 26 }} />
          <span style={{ color: '#2e3138', fontSize: 14, fontWeight: 600, letterSpacing: 0.5 }}>{t('header.platformName')}</span>
          {isProjectPage && projectName && (
            <>
              <span style={{ color: '#e0e0e3', margin: '0 4px' }}>/</span>
              <span style={{ color: '#8c919e', fontSize: 13 }}>{projectName}</span>
              <BranchSelector projectId={projectId} />
            </>
          )}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <ServiceStatusBadge />
          <Tooltip title={lang === 'zh' ? '简体中文 → English' : 'English → 简体中文'}>
            <Button type="text" size="small" icon={<GlobalOutlined style={{ color: '#7cacf8' }} />}
              onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')} />
          </Tooltip>
          <Tooltip title={lang === 'zh' ? '通知' : 'Notifications'}>
            <Button type="text" icon={<BellOutlined style={{ color: '#f5b971' }} />} size="small" />
          </Tooltip>
          <Dropdown menu={userMenu} placement="bottomRight">
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer' }}>
              <Avatar size={24} style={{ background: 'rgba(124,172,248,0.15)', color: '#7cacf8', fontSize: 11, border: '1.5px solid rgba(124,172,248,0.3)' }}>{displayName[0]}</Avatar>
              <span style={{ color: '#8c919e', fontSize: 13 }}>{displayName}</span>
            </div>
          </Dropdown>
        </div>
      </Header>

      <Layout>
        <Sider
          width={200}
          collapsedWidth={52}
          collapsed={collapsed}
          theme="light"
          style={{ background: 'rgba(255,255,255,0.2)', borderRight: '1px solid rgba(0,0,0,0.04)', display: 'flex', flexDirection: 'column', backdropFilter: 'blur(8px)', WebkitBackdropFilter: 'blur(8px)' }}
        >
          <div style={{ flex: 1, overflow: 'auto' }}>
            <Menu
              mode="inline"
              selectedKeys={[location.pathname]}
              openKeys={collapsed ? [] : openKeys}
              onOpenChange={handleOpenChange}
              items={menuItems}
              onClick={({ key }) => navigate(key)}
              style={{ border: 'none', fontSize: 13, paddingTop: 8, background: 'transparent' }}
            />
          </div>
          <div style={{ padding: '8px 6px', borderTop: '1px solid rgba(0,0,0,0.04)' }}>
            <Button
              type="text"
              icon={collapsed ? <MenuUnfoldOutlined /> : <MenuFoldOutlined />}
              onClick={() => setCollapsed(!collapsed)}
              style={{ width: '100%', color: '#bfc4cd' }}
              size="small"
            />
          </div>
        </Sider>

        <Content className="app-content-area" style={{ padding: '12px 16px', background: 'transparent', overflow: 'auto', minHeight: 'calc(100vh - 46px)' }}>
          <Routes>
            <Route path="/" element={<Navigate to="/projects" replace />} />
            <Route path="/projects" element={<ProjectList />} />
            <Route path="/projects/:projectId/cases" element={<CaseManagement />} />
            <Route path="/projects/:projectId/review-report" element={<ReviewReport />} />
            <Route path="/projects/:projectId/cases/:caseId" element={<CaseDetail />} />
            <Route path="/projects/:projectId/apis" element={<ApiManagement />} />
            <Route path="/projects/:projectId/plans" element={<PlanList />} />
            <Route path="/projects/:projectId/plans/:planId" element={<PlanDetail />} />
            <Route path="/projects/:projectId/plans/:planId/manual-record" element={<ManualRecord />} />
            <Route path="/projects/:projectId/reports" element={<ReportList />} />
            <Route path="/projects/:projectId/reports/:reportId" element={<ReportDetail />} />
            <Route path="/projects/:projectId/logs" element={<AuditLogs />} />
            <Route path="/projects/:projectId/settings/ai" element={<ProjectAIConfig />} />
            <Route path="/projects/:projectId/settings/automation-data" element={<AutomationData />} />
            <Route path="/projects/:projectId/settings/env" element={<EnvConfig />} />
            <Route path="/projects/:projectId/settings/i18n" element={<I18nMessages />} />
            <Route path="/projects/:projectId/settings/ai-capabilities" element={<AICapabilities />} />
            <Route path="/projects/:projectId/settings/skills" element={<SkillManage />} />
            <Route path="/projects/:projectId/settings/mcp-tools" element={<MCPTools />} />
            <Route path="/projects/:projectId/exploratory" element={<Exploratory />} />
            <Route path="/projects/:projectId/documents" element={<Documents />} />
            {/* 「接口测试」模块 2026-08-15 下线（见 docs/cc-platform-loop-spec.md §11）。
                留一条重定向而不是直接删路由：全站没有兜底 404，存了书签的人点进来
                会看到一片空白内容区 —— 不报错也不说话，比 404 还难判断发生了什么。
                接口场景现在只在「用例详情 → 接口测试」页签里维护。 */}
            <Route path="/projects/:projectId/api-test" element={<RedirectToCases />} />
            <Route path="/settings/services" element={<SystemServices />} />
            {/* 环境 2026-08-21 起是项目级的（docs/data-scoping-and-isolation.md §4）。
                旧的 /settings/env 留一条跳转：书签点进来时给项目列表，而不是白屏。 */}
            <Route path="/settings/env" element={<Navigate to="/projects" replace />} />
            <Route path="/settings/channels" element={<ChannelConfig />} />
            <Route path="/settings/ai-providers" element={<AIProviderConfig />} />
            <Route path="/settings/users" element={<UserManagement />} />
            <Route path="/settings/logs" element={<AuditLogs />} />
            <Route path="/tools/llm-mock" element={<LlmMock />} />
            <Route path="/tools/api-mock" element={<ApiMock />} />
            <Route path="/tools/proxy-probe" element={<ProxyProbe />} />
            <Route path="/tools/mcp-mock" element={<McpMock />} />
            <Route path="/tools/oauth2-mock" element={<OAuth2Mock />} />
            <Route path="/tools/toolbox" element={<Toolbox />} />
            <Route path="/tools/http-client" element={<HttpClient />} />
            <Route path="/tools/load-test" element={<LoadTest />} />
          </Routes>
        </Content>
      </Layout>

      <Modal title="修改密码" open={pwdOpen} onOk={handleChangePassword} onCancel={() => setPwdOpen(false)}
        okText="确认修改" cancelText="取消" confirmLoading={pwdLoading} width={400}>
        <Form form={pwdForm} layout="vertical" style={{ marginTop: 16 }}>
          <Form.Item name="oldPassword" label="原密码" rules={[{ required: true, message: '请输入原密码' }]}>
            <Input.Password placeholder="请输入当前密码" />
          </Form.Item>
          <Form.Item name="newPassword" label="新密码" rules={[{ required: true, message: '请输入新密码' }, { min: 6, message: '密码至少 6 位' }]}>
            <Input.Password placeholder="请输入新密码（至少 6 位）" />
          </Form.Item>
          <Form.Item name="confirmPassword" label="确认新密码"
            dependencies={['newPassword']}
            rules={[
              { required: true, message: '请确认新密码' },
              ({ getFieldValue }) => ({ validator(_, value) {
                if (!value || getFieldValue('newPassword') === value) return Promise.resolve()
                return Promise.reject(new Error('两次输入的密码不一致'))
              }}),
            ]}>
            <Input.Password placeholder="请再次输入新密码" />
          </Form.Item>
        </Form>
      </Modal>
    </Layout>
  )
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/*" element={<RequireAuth><AppLayout /></RequireAuth>} />
    </Routes>
  )
}
