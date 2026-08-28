"""权限点模型 —— 唯一事实源（渐进三层）。

    第 1 层 系统角色  admin(全权) / operator(运营·跨项目只读) / user(普通)
    第 2 层 项目角色  manager / member / viewer
    第 3 层 权限点    声明式；**角色 = 权限点集合**（本轮固定映射）

三处消费同一份映射：后端校验（deps/permissions.require_permission）、前端菜单
（usePermissions）、AI 助手能力面（用户权限点 ∩ 页面动作）。不再各写一份、各自漂移。

**兼容期**：项目角色同时认新旧两套名字 —— 新 manager/member/viewer 与旧
project_admin/developer/tester/guest 都能解析。存量数据、存量测试、以及现有
require_project_role(...) 守卫用的旧名照常工作；将来真正改名只是一次纯数据迁移
（UPDATE project_members.role），本文件与三处消费端一行不改。这正是「渐进：先固化 + 留接口」。

权限集合按**当前端点守卫的真实分档**标定（见 audit：项目路由的 require_project_role 元组分 4 桶）：
    ('project_admin',)                         → manager 专属（成员管理/项目设置/分支生命周期…）
    ('project_admin','developer')              → member 及以上（doc 删除/自动化资源/i18n/mcp 范围…）
    ('project_admin','developer','tester')     → tester 及以上（用例/环境/知识/AI 造/评审… 主体写）
    ('project_admin','developer','tester','guest'|'viewer') → viewer 及以上（所有读）

注意 tester 现状比 developer 少一档（拿不到上面第二桶），所以 tester 单列一个集合，
忠实反映当前能力；PRD 决策 4「member = developer+tester 合并、去掉 tester 不该写的区分」
落地时，把 tester 数据迁到 member 即获得该档 —— 前向一致，不是丢权限。
"""
from __future__ import annotations

# ── 权限点常量 ──────────────────────────────────────────────────
# 项目级
P_PROJECT_READ = "project.read"          # 看项目/分支/用例/报告/文档/环境(非密) 等一切读
P_CASE_WRITE = "case.write"              # 用例/文件夹/接口节点/接口场景 增删改、导入
P_CASE_GENERATE = "case.generate"        # AI 造用例/脚本、AI 评审、场景生成
P_PLAN_RUN = "plan.run"                  # 跑接口场景 / 跑脚本 / 执行
P_REPORT_WRITE = "report.write"          # 删报告
P_ENV_WRITE = "env.write"                # 环境 + 全局变量 增删改
P_KNOWLEDGE_WRITE = "knowledge.write"    # 知识库 增删
P_AICONFIG_WRITE = "aiconfig.write"      # 项目 AI 档位 选择/自建/删除
P_DOC_GENERATE = "doc.generate"          # 文档 AI 生成/优化
P_DOC_MANAGE = "doc.manage"             # 文档删除 + 自动化资源 + i18n + 文件夹重挂 + MCP 范围（developer 档）
P_MEMBER_MANAGE = "member.manage"        # 成员增删改
P_PROJECT_SETTINGS = "project.settings"  # 项目设置/分支生命周期/计划归档/QA 对账配置/文件夹删除/项目 skill 删除

# 系统级
P_PROJECT_CREATE = "project.create"          # 建项目（任意登录用户）
P_SYS_USER_MANAGE = "system.user.manage"     # 用户管理
P_SYS_CHANNEL_READ = "system.channel.read"   # 看通知渠道
P_SYS_CHANNEL_MANAGE = "system.channel.manage"  # 改通知渠道
P_SYS_PROVIDER_READ = "system.provider.read"    # 看 AI provider
P_SYS_PROVIDER_MANAGE = "system.provider.manage"  # 改 AI provider
P_SYS_SKILL_MANAGE = "system.skill.manage"      # 改预置 skill
P_SYS_SERVICE_READ = "system.service.read"      # 看服务监控

# 全部权限点（admin 解析成这一整套）
ALL_PERMISSIONS: frozenset[str] = frozenset({
    P_PROJECT_READ, P_CASE_WRITE, P_CASE_GENERATE, P_PLAN_RUN, P_REPORT_WRITE,
    P_ENV_WRITE, P_KNOWLEDGE_WRITE, P_AICONFIG_WRITE, P_DOC_GENERATE, P_DOC_MANAGE,
    P_MEMBER_MANAGE, P_PROJECT_SETTINGS,
    P_PROJECT_CREATE, P_SYS_USER_MANAGE, P_SYS_CHANNEL_READ, P_SYS_CHANNEL_MANAGE,
    P_SYS_PROVIDER_READ, P_SYS_PROVIDER_MANAGE, P_SYS_SKILL_MANAGE, P_SYS_SERVICE_READ,
})

# ── 项目角色 → 权限点集合（单调递增：viewer ⊂ tester ⊂ member ⊂ manager）──
_VIEWER = frozenset({P_PROJECT_READ})
_TESTER = _VIEWER | frozenset({
    P_CASE_WRITE, P_CASE_GENERATE, P_PLAN_RUN, P_REPORT_WRITE,
    P_ENV_WRITE, P_KNOWLEDGE_WRITE, P_AICONFIG_WRITE, P_DOC_GENERATE,
})
_MEMBER = _TESTER | frozenset({P_DOC_MANAGE})
_MANAGER = _MEMBER | frozenset({P_MEMBER_MANAGE, P_PROJECT_SETTINGS})

# 新名 + 旧名都映射到同一集合（兼容期）
PROJECT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "viewer": _VIEWER, "guest": _VIEWER,
    "tester": _TESTER,
    "member": _MEMBER, "developer": _MEMBER,
    "manager": _MANAGER, "project_admin": _MANAGER,
}

# ── 系统角色 → 权限点集合 ──
# 任意登录用户都能建项目（create_project 现为 _AUTHED，任何 user 可建）。
_USER_SYS = frozenset({P_PROJECT_CREATE})
# operator：跨项目只读 + 平台设施只读；**不含**任何写、不含密码类变量（后者留待 env 密文分级时接）。
# 跨项目只读的「绕过项目成员」强制尚未接线 —— 这里先把它的权限面固化，enforcement 随 operator 正式启用再补。
_OPERATOR_SYS = _USER_SYS | frozenset({
    P_PROJECT_READ, P_SYS_CHANNEL_READ, P_SYS_PROVIDER_READ, P_SYS_SERVICE_READ,
})
SYSTEM_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "user": _USER_SYS,
    "operator": _OPERATOR_SYS,
    "admin": ALL_PERMISSIONS,  # 实际走 resolve 里的 admin 直通；此处列全集是为自洽
}

# 合法角色取值（供 DB CheckConstraint / Pydantic 校验共用同一份，避免两处漂移）
SYSTEM_ROLES: tuple[str, ...] = ("admin", "operator", "user")
PROJECT_ROLES_ALL: tuple[str, ...] = (
    "manager", "member", "viewer",              # 新
    "project_admin", "developer", "tester", "guest",  # 旧（兼容期）
)

# 项目角色归一：把新旧两套名折叠到同一个规范名，供 require_project_role 做等价匹配。
# 这样一个 role="manager" 的成员满足 require_project_role("project_admin") 这类**旧名**守卫，
# 反过来一个 role="guest" 的成员也满足 scenario_gen 里 READ_ROLES 那种**已经用新名 viewer** 的守卫。
# 不做归一就会两头漏：既有存量端点全用旧名、又有新端点开始用新名，同一个成员在两拨端点上表现不一致
# （实测：guest 成员当前读不了 scenario-gen 统计，因为那几条守卫写的是 viewer）。
# 注意 tester 单独保留 —— 它比 member 低一档（见顶部分档说明），不折叠进 member。
_CANONICAL_PROJECT_ROLE: dict[str, str] = {
    "manager": "manager", "project_admin": "manager",
    "member": "member", "developer": "member",
    "tester": "tester",
    "viewer": "viewer", "guest": "viewer",
}


def canonical_project_role(role: str | None) -> str | None:
    """把项目角色折叠成规范名（新旧同名 → 同一个）。未知角色原样返回（默认拒绝时不会误放行）。"""
    if role is None:
        return None
    return _CANONICAL_PROJECT_ROLE.get(role, role)


def system_permissions(system_role: str) -> frozenset[str]:
    return SYSTEM_ROLE_PERMISSIONS.get(system_role, frozenset())


def project_permissions(project_role: str | None) -> frozenset[str]:
    if not project_role:
        return frozenset()
    return PROJECT_ROLE_PERMISSIONS.get(project_role, frozenset())


def resolve_permissions(system_role: str, project_role: str | None = None) -> frozenset[str]:
    """把（系统角色, 项目角色）解析成权限点集合。

    - 系统 admin：全权，直接返回 ALL_PERMISSIONS（对齐 require_project_role 的 admin 直通）。
    - 其余：系统权限 ∪ 项目权限（项目角色为 None 表示不在该项目语境里，只给系统权限）。

    留接口：将来 user_permissions 单用户直授表，就在这里并上 user_overrides(user_id)，
    三处消费端不用改。
    """
    if system_role == "admin":
        return ALL_PERMISSIONS
    return system_permissions(system_role) | project_permissions(project_role)


def has_permission(system_role: str, project_role: str | None, permission: str) -> bool:
    return permission in resolve_permissions(system_role, project_role)
