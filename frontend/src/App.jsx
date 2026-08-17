import { useState, useEffect } from 'react'
import { Routes, Route, useNavigate, useLocation, Navigate, useParams } from 'react-router-dom'
import { Layout, Menu, Avatar, Dropdown, Button, Tooltip, message, Modal, Form, Input } from 'antd'
import {
  FolderOutlined, FileTextOutlined, UnorderedListOutlined, BarChartOutlined,
  SettingOutlined, UserOutlined, FileSearchOutlined, ApiOutlined,
  MenuFoldOutlined, MenuUnfoldOutlined, BellOutlined, RobotOutlined,
  ThunderboltOutlined, BugOutlined, ToolOutlined, SendOutlined,
  NodeIndexOutlined,
  GlobalOutlined, SafetyCertificateOutlined, DatabaseOutlined, TranslationOutlined,
  DeploymentUnitOutlined,
} from '@ant-design/icons'
import { api } from './utils/request'
import { useLang } from './utils/i18n.jsx'
import BranchSelector from './components/BranchSelector'
import ServiceStatusBadge from './components/ServiceStatusBadge'
import ProjectList from './pages/projects/ProjectList'
import CaseManagement from './pages/cases/CaseManagement'
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

  // 侧边栏分组。三条规则，改的时候别破坏：
  //
  // 1. **用 group 不用可折叠子菜单**。原来 Mock/工具/AI 是 SubMenu，却又靠
  //    defaultOpenKeys 默认全展开 —— 等于给分组加了层壳，只多一次误折叠的机会。
  //    更糟的是「AI 智能」在系统菜单下只挂一个子项，多一层等于没分级。
  // 2. **同一层里按"干这件事的顺序"排，不按加进来的先后**。原来用例/接口/计划/
  //    报告/探索/文档/自动化数据/国际化词典平铺八条，看不出哪些是一类。
  // 3. **分组标题走 i18n**，别硬编码中文 —— 上一版有一半标题是写死的，
  //    切到英文只翻了一半，那才是最像"没做完"的地方。
  const menuItems = isProjectPage ? [
    { key: '/projects', icon: <FolderOutlined />, label: t('menu.back') },
    {
      type: 'group', key: 'g-design', label: t('menu.group.design'),
      children: [
        { key: `/projects/${projectId}/cases`, icon: <FileTextOutlined />, label: t('menu.cases') },
        { key: `/projects/${projectId}/apis`, icon: <ApiOutlined />, label: t('menu.apis') },
      ],
    },
    {
      type: 'group', key: 'g-exec', label: t('menu.group.exec'),
      children: [
        { key: `/projects/${projectId}/plans`, icon: <UnorderedListOutlined />, label: t('menu.plans') },
        { key: `/projects/${projectId}/reports`, icon: <BarChartOutlined />, label: t('menu.reports') },
        { key: `/projects/${projectId}/exploratory`, icon: <BugOutlined />, label: t('menu.exploratory') },
        { key: `/projects/${projectId}/documents`, icon: <FileTextOutlined />, label: t('menu.documents') },
      ],
    },
    {
      type: 'group', key: 'g-ai', label: t('menu.group.ai'),
      children: [
        { key: `/projects/${projectId}/settings/ai-capabilities`, icon: <ThunderboltOutlined />, label: t('menu.ai.capabilities') },
        { key: `/projects/${projectId}/settings/skills`, icon: <FileTextOutlined />, label: t('menu.ai.skills') },
        { key: `/projects/${projectId}/settings/mcp-tools`, icon: <ApiOutlined />, label: t('menu.ai.mcp') },
        { key: `/projects/${projectId}/settings/ai`, icon: <SettingOutlined />, label: t('menu.ai.config') },
      ],
    },
    {
      type: 'group', key: 'g-proj-config', label: t('menu.group.projectConfig'),
      children: [
        { key: `/projects/${projectId}/settings/automation-data`, icon: <DatabaseOutlined />, label: t('menu.automationData') },
        { key: `/projects/${projectId}/settings/i18n`, icon: <TranslationOutlined />, label: t('menu.i18nDict') },
        { key: `/projects/${projectId}/logs`, icon: <FileSearchOutlined />, label: t('menu.logs') },
      ],
    },
  ] : [
    { key: '/projects', icon: <FolderOutlined />, label: t('menu.projects') },
    // 工具排在设置前面：进不了项目的时候，来这儿多半是用 Mock 或调接口，不是改配置
    {
      type: 'group', key: 'g-mock', label: t('menu.group.mock'),
      children: [
        { key: '/tools/api-mock', icon: <GlobalOutlined />, label: t('menu.apiMock') },
        { key: '/tools/llm-mock', icon: <RobotOutlined />, label: t('menu.llmMock') },
        { key: '/tools/mcp-mock', icon: <ApiOutlined />, label: t('menu.mcpMock') },
        { key: '/tools/oauth2-mock', icon: <SafetyCertificateOutlined />, label: t('menu.oauth2Mock') },
      ],
    },
    {
      type: 'group', key: 'g-tools', label: t('menu.group.tools'),
      children: [
        { key: '/tools/http-client', icon: <SendOutlined />, label: t('menu.httpClient') },
        { key: '/tools/load-test', icon: <ThunderboltOutlined />, label: t('menu.loadTest') },
        { key: '/tools/proxy-probe', icon: <NodeIndexOutlined />, label: t('menu.proxyProbe') },
        { key: '/tools/toolbox', icon: <ToolOutlined />, label: t('menu.toolbox') },
      ],
    },
    {
      type: 'group', key: 'g-system', label: t('menu.group.system'),
      children: [
        { key: '/settings/env', icon: <SettingOutlined />, label: t('menu.envConfig') },
        { key: '/settings/channels', icon: <BellOutlined />, label: t('menu.channels') },
        // 原来它被塞在一个只有它一个子项的「AI 智能」子菜单里
        { key: '/settings/ai-providers', icon: <RobotOutlined />, label: t('menu.aiProviders') },
        ...(user.role === 'admin' ? [
          { key: '/settings/users', icon: <UserOutlined />, label: t('menu.users') },
        ] : []),
      ],
    },
    {
      type: 'group', key: 'g-ops', label: t('menu.group.ops'),
      children: [
        { key: '/settings/services', icon: <DeploymentUnitOutlined />, label: t('menu.services') },
        { key: '/settings/logs', icon: <FileSearchOutlined />, label: t('menu.logs') },
      ],
    },
  ]

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
        /* 收起侧边栏时把分组标题藏掉。不藏的话 antd 会把它按 52px 宽截断，
           侧栏上冒出「测…」「执…」「AI …」「项…」四个看不懂的残字。
           留一条细分隔线顶替，分组的边界还在，只是不写字了。 */
        .ant-menu-inline-collapsed .ant-menu-item-group-title {
          height: 0;
          padding: 0;
          margin: 6px 10px;
          overflow: hidden;
          border-top: 1px solid rgba(0,0,0,0.06);
        }
        /* 第一个分组紧挨着「项目列表」，再加一条线就是双线了 */
        .ant-menu-inline-collapsed .ant-menu-item-group:first-of-type .ant-menu-item-group-title {
          border-top: none;
          margin-top: 0;
        }
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
              /* 没有 defaultOpenKeys 了 —— 分组用的是 group 而不是可折叠子菜单，
                 收起侧边栏时 antd 会自己把分组标题藏掉，只留图标 */
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
            <Route path="/settings/env" element={<EnvConfig />} />
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
