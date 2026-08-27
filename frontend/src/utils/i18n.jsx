import { createContext, useContext, useState, useCallback } from 'react'

const LangContext = createContext({ lang: 'zh', t: (k) => k, setLang: () => {} })

const MESSAGES = {
  zh: {
    // 菜单
    'menu.projects': '项目列表',
    'menu.cases': '用例管理',
    // 菜单项 2026-08-27 下掉，路由和页面保留 —— 键留着，恢复入口时不用再翻一遍
    'menu.apis': 'API 接口',
    'menu.qaCatalog': 'QA 对账',
    'menu.plans': '测试计划',
    'menu.reports': '测试报告',
    'menu.reviewReport': '审核报告',
    'menu.exploratory': '探索测试',
    'menu.ai': 'AI 智能',
    'menu.ai.capabilities': '能力总览',
    'menu.ai.scenarioGen': 'AI 生成用例',
    'menu.ai.skills': 'Skill 管理',
    'menu.ai.mcp': 'MCP 工具',
    'menu.ai.config': 'AI 配置',
    'menu.logs': '操作日志',
    'menu.back': '返回项目列表',
    'menu.services': '服务与端口',
    'menu.envConfig': '环境配置',
    'menu.channels': '通知渠道',
    'menu.aiProviders': 'AI 服务配置',
    'menu.users': '用户管理',
    'menu.llmMock': 'LLM Mock',
    'menu.apiMock': '协议 Mock',
    'menu.mcpMock': 'MCP Mock',
    'menu.toolbox': '工具箱',
    'menu.httpClient': 'HTTP 请求',
    'menu.loadTest': '压力测试',
    'menu.oauth2Mock': 'OAuth2 Mock',
    'menu.proxyProbe': '代理观测',
    'menu.automationData': '自动化数据',
    'menu.i18nDict': '国际化词典',
    // 一级菜单标题。按功能实际作用在什么上分，不按名字听起来像什么 —— 理由写在 App.jsx
    'menu.group.design': '测试设计',
    'menu.group.exec': '执行与产出',
    'menu.group.ai': 'AI 能力',
    'menu.group.projectConfig': '项目配置',
    'menu.group.project': '项目管理',
    'menu.group.tools': '测试工具',
    'menu.group.system': '系统管理',

    // 通用
    'common.save': '保存',
    'common.cancel': '取消',
    'common.delete': '删除',
    'common.edit': '编辑',
    'common.create': '新建',
    'common.search': '搜索',
    'common.export': '导出',
    'common.import': '导入',
    'common.loading': '加载中...',
    'common.confirm': '确认',
    'common.success': '操作成功',
    'common.admin': '管理员',

    // 用例
    'cases.title': '用例管理',
    'cases.create': '新建用例',
    'cases.aiGenerate': 'AI 生成用例',
    'cases.aiScript': 'AI 生成脚本',
    'cases.aiReview': 'AI 评审',
    'cases.sync': '同步用例',

    // 项目
    'projects.title': '项目列表',
    'projects.create': '创建项目',
    'projects.members': '成员',
    'projects.settings': '设置',

    // 登录
    'login.title': '测试管理平台',
    'login.subtitle': 'Lumiere - 统一测试管理与执行',
    'login.username': '用户名',
    'login.password': '密码',
    'login.submit': '登 录',
    'login.success': '登录成功',

    // Header
    'header.platformName': 'Lumiere 测试管理平台',
    'header.changePassword': '修改密码',
    'header.logout': '退出登录',
  },
  en: {
    'menu.projects': 'Projects',
    'menu.cases': 'Test Cases',
    'menu.apis': 'API Endpoints',
    'menu.qaCatalog': 'QA Coverage',
    'menu.plans': 'Test Plans',
    'menu.reports': 'Test Reports',
    'menu.reviewReport': 'Review Report',
    'menu.exploratory': 'Exploratory',
    'menu.ai': 'AI',
    'menu.ai.capabilities': 'Capabilities',
    'menu.ai.scenarioGen': 'Scenario Gen',
    'menu.ai.skills': 'Skills',
    'menu.ai.mcp': 'MCP Tools',
    'menu.ai.config': 'AI Config',
    'menu.logs': 'Audit Logs',
    'menu.back': 'Back to Projects',
    'menu.services': 'Services & Ports',
    'menu.envConfig': 'Environments',
    'menu.channels': 'Notifications',
    'menu.aiProviders': 'AI Providers',
    'menu.users': 'Users',
    'menu.llmMock': 'LLM Mock',
    'menu.apiMock': 'Protocol Mock',
    'menu.mcpMock': 'MCP Mock',
    'menu.toolbox': 'Toolbox',
    'menu.httpClient': 'HTTP Client',
    'menu.loadTest': 'Load Test',
    'menu.oauth2Mock': 'OAuth2 Mock',
    'menu.proxyProbe': 'Proxy Capture',
    'menu.automationData': 'Automation Data',
    'menu.i18nDict': 'i18n Dictionary',
    'menu.group.design': 'Test Design',
    'menu.group.exec': 'Run & Output',
    'menu.group.ai': 'AI',
    'menu.group.projectConfig': 'Project Config',
    'menu.group.project': 'Projects',
    'menu.group.tools': 'Test Tools',
    'menu.group.system': 'System',

    'common.save': 'Save',
    'common.cancel': 'Cancel',
    'common.delete': 'Delete',
    'common.edit': 'Edit',
    'common.create': 'Create',
    'common.search': 'Search',
    'common.export': 'Export',
    'common.import': 'Import',
    'common.loading': 'Loading...',
    'common.confirm': 'Confirm',
    'common.success': 'Success',
    'common.admin': 'Admin',

    'cases.title': 'Test Cases',
    'cases.create': 'New Case',
    'cases.aiGenerate': 'AI Generate Cases',
    'cases.aiScript': 'AI Generate Script',
    'cases.aiReview': 'AI Review',
    'cases.sync': 'Sync Cases',

    'projects.title': 'Projects',
    'projects.create': 'Create Project',
    'projects.members': 'Members',
    'projects.settings': 'Settings',

    'login.title': 'Test Management Platform',
    'login.subtitle': 'Lumiere - Unified Test Management',
    'login.username': 'Username',
    'login.password': 'Password',
    'login.submit': 'Login',
    'login.success': 'Login successful',

    'header.platformName': 'Lumiere',
    'header.changePassword': 'Change Password',
    'header.logout': 'Logout',
  },
}

export function LangProvider({ children }) {
  const [lang, setLang] = useState(() => localStorage.getItem('lang') || 'zh')

  const t = useCallback((key) => {
    return MESSAGES[lang]?.[key] || MESSAGES.zh[key] || key
  }, [lang])

  const changeLang = useCallback((newLang) => {
    setLang(newLang)
    localStorage.setItem('lang', newLang)
  }, [])

  return (
    <LangContext.Provider value={{ lang, t, setLang: changeLang }}>
      {children}
    </LangContext.Provider>
  )
}

export function useLang() {
  return useContext(LangContext)
}
