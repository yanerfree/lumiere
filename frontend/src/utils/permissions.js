// 权限点常量 —— 与后端 app/core/permissions.py 的字符串一一对应。
// 后端 GET /api/me/permissions 返回当前用户在（某项目下）持有的权限点列表，
// 前端菜单/按钮据此收口。**这是 UX 层收口，真正的强制在后端**：即便前端漏挡一个按钮，
// 后端守卫也会 403。所以这里少挡一个是体验问题，不是越权。
export const PERM = {
  // 项目级
  PROJECT_READ: 'project.read',
  CASE_WRITE: 'case.write',
  CASE_GENERATE: 'case.generate',
  PLAN_RUN: 'plan.run',
  REPORT_WRITE: 'report.write',
  ENV_WRITE: 'env.write',
  KNOWLEDGE_WRITE: 'knowledge.write',
  AICONFIG_WRITE: 'aiconfig.write',
  DOC_GENERATE: 'doc.generate',
  DOC_MANAGE: 'doc.manage',
  MEMBER_MANAGE: 'member.manage',
  PROJECT_SETTINGS: 'project.settings',
  // 系统级
  PROJECT_CREATE: 'project.create',
  SYS_USER_MANAGE: 'system.user.manage',
  SYS_CHANNEL_READ: 'system.channel.read',
  SYS_CHANNEL_MANAGE: 'system.channel.manage',
  SYS_PROVIDER_READ: 'system.provider.read',
  SYS_PROVIDER_MANAGE: 'system.provider.manage',
  SYS_SKILL_MANAGE: 'system.skill.manage',
  SYS_SERVICE_READ: 'system.service.read',
  // 「工具」整组（mock/压测/抓包/HTTP 客户端…）的入口权限。
  // 这一组此前**一个 perm 都没挂** —— 于是游客也能看见八个入口，点进去全 403。
  SYS_TOOLS_USE: 'system.tools.use',
}

// 拼接 /me/permissions 请求路径（带上或不带项目语境）
export function mePermissionsPath(projectId) {
  return projectId ? `/me/permissions?project_id=${projectId}` : '/me/permissions'
}
