import { createContext, useContext, useState, useCallback } from 'react'

const LangContext = createContext({ lang: 'zh', t: (k) => k, setLang: () => {} })

const MESSAGES = {
  zh: {
    // 菜单
    'menu.projects': '项目列表',
    'menu.cases': '用例管理',
    'menu.apis': 'API 接口',
    'menu.plans': '测试计划',
    'menu.reports': '测试报告',
    'menu.exploratory': '探索测试',
    'menu.documents': '文档管理',
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
    'login.subtitle': 'TestBench - 统一测试管理与执行',
    'login.username': '用户名',
    'login.password': '密码',
    'login.submit': '登 录',
    'login.success': '登录成功',

    // Header
    'header.platformName': '测试管理平台',
    'header.changePassword': '修改密码',
    'header.logout': '退出登录',
  },
  en: {
    'menu.projects': 'Projects',
    'menu.cases': 'Test Cases',
    'menu.apis': 'API Endpoints',
    'menu.plans': 'Test Plans',
    'menu.reports': 'Test Reports',
    'menu.exploratory': 'Exploratory',
    'menu.documents': 'Documents',
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
    'menu.apiMock': '协议 Mock',
    'menu.mcpMock': 'MCP Mock',
    'menu.toolbox': '工具箱',
    'menu.httpClient': 'HTTP 请求',
    'menu.loadTest': '压力测试',

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
    'login.subtitle': 'TestBench - Unified Test Management',
    'login.username': 'Username',
    'login.password': 'Password',
    'login.submit': 'Login',
    'login.success': 'Login successful',

    'header.platformName': 'TestBench',
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
