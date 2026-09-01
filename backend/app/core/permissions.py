"""权限点模型 —— 唯一事实源（渐进三层）。

    第 1 层 系统角色  admin(全权) / user(普通) / guest(游客·硬封顶只读)
    第 2 层 项目角色  manager(项目管理员) / member(成员)
    第 3 层 权限点    声明式；**角色 = 权限点集合**

三处消费同一份映射：后端校验（deps/permissions.require_permission）、前端菜单
（usePermissions）、AI 助手能力面（用户权限点 ∩ 页面动作）。不再各写一份、各自漂移。

2026-08-29 重定（见 docs/permission-audit-2026-08.md「决策」一节）：
- 项目角色从 4 档砍到 2 档。审计实测 developer 与 tester 只差 13 个端点、
  权限点上只差一个 doc.manage —— 4 档名不副实，真正起作用的只有「能改 / 只读」两档。
- **只读语义从项目层上移到账号层**：原 viewer 的位置由系统角色 guest 承担。
- operator 删除。它自报 project.read 却没有任何强制路径认它（登录后看到 0 个项目），
  是个空壳；与其接线不如去掉 —— 「自报一套、强制另一套」是本模型最该避免的形状。

**有效权限 = (系统权限 ∪ 项目权限) ∩ CEILING[系统角色]**

封顶只解决「呈现」和「防御纵深」。**真正的强制在 app/core/readonly_gate.py + deps/auth.py
的非 GET 闸门**（因为 /api 下 264 条写路由里有 129 条根本没有项目语境，权限点挂不上去）。
两条腿都要在 —— 只有封顶没闸门，就是又造一个 operator。

**兼容期**：项目角色仍认旧名（project_admin/developer/tester/guest → manager/member），
存量 token、存量数据、以及尚未改写的守卫照常工作。canonical_project_role 对未知名
原样返回（默认拒绝时不会误放行）。
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
P_SYS_TOOLS_USE = "system.tools.use"            # 工具组（mock/压测/http-client/toolbox…）
P_SYS_FEEDBACK_MANAGE = "system.feedback.manage"  # CC 反馈（外部 CC 报回来的平台自身问题）

# 全部权限点（admin 解析成这一整套）
ALL_PERMISSIONS: frozenset[str] = frozenset({
    P_PROJECT_READ, P_CASE_WRITE, P_CASE_GENERATE, P_PLAN_RUN, P_REPORT_WRITE,
    P_ENV_WRITE, P_KNOWLEDGE_WRITE, P_AICONFIG_WRITE, P_DOC_GENERATE, P_DOC_MANAGE,
    P_MEMBER_MANAGE, P_PROJECT_SETTINGS,
    P_PROJECT_CREATE, P_SYS_USER_MANAGE, P_SYS_CHANNEL_READ, P_SYS_CHANNEL_MANAGE,
    P_SYS_PROVIDER_READ, P_SYS_PROVIDER_MANAGE, P_SYS_SKILL_MANAGE, P_SYS_SERVICE_READ,
    P_SYS_TOOLS_USE, P_SYS_FEEDBACK_MANAGE,
})

# ── 项目角色 → 权限点集合（单调递增：member ⊂ manager）──
# 只读不再是项目角色 —— 它由系统角色 guest 的封顶承担（见 SYSTEM_ROLE_CEILING）。
_MEMBER = frozenset({
    P_PROJECT_READ,
    P_CASE_WRITE, P_CASE_GENERATE, P_PLAN_RUN, P_REPORT_WRITE,
    P_ENV_WRITE, P_KNOWLEDGE_WRITE, P_AICONFIG_WRITE, P_DOC_GENERATE,
    P_DOC_MANAGE,
})
_MANAGER = _MEMBER | frozenset({P_MEMBER_MANAGE, P_PROJECT_SETTINGS})

# 新名 + 旧名都映射到同一集合（兼容期）。
# 旧的 tester 档比 developer 少一个 doc.manage —— 合并后 tester 数据迁到 member 即获得该档，
# 是前向一致的加权，不是丢权限（PRD 决策 4）。
PROJECT_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "member": _MEMBER, "developer": _MEMBER, "tester": _MEMBER,
    "manager": _MANAGER, "project_admin": _MANAGER,
}

# ── 系统角色 → 权限点集合 ──
# 任意登录用户都能建项目 + 用工具组。
_USER_SYS = frozenset({P_PROJECT_CREATE, P_SYS_TOOLS_USE})
# 游客：系统权限为空 —— 不能建项目、不碰平台设施、不进工具组。
# 它的 project.read 来自「在某项目里挂 member」，再被下面的封顶削成只读。
# 这样「游客且不在任何项目」= 零权限，而不是凭空能读。
_GUEST_SYS: frozenset[str] = frozenset()
SYSTEM_ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "user": _USER_SYS,
    "guest": _GUEST_SYS,
    "admin": ALL_PERMISSIONS,  # 实际走 resolve 里的 admin 直通；此处列全集是为自洽
}

# 只有 admin 拿得到的权限点 —— 供封样测试反查「孤儿权限点」。
# （不能靠 ALL_PERMISSIONS 与各角色并集比较：admin 本身就映射到全集，那个断言恒真。）
ADMIN_ONLY_PERMISSIONS: frozenset[str] = frozenset({
    P_SYS_USER_MANAGE, P_SYS_CHANNEL_READ, P_SYS_CHANNEL_MANAGE,
    P_SYS_PROVIDER_READ, P_SYS_PROVIDER_MANAGE, P_SYS_SKILL_MANAGE,
    P_SYS_SERVICE_READ, P_SYS_FEEDBACK_MANAGE,
})

# ── 系统角色封顶 ──────────────────────────────────────────────────
# 有效权限 = (系统权限 ∪ 项目权限) ∩ CEILING[系统角色]
# 默认值必须是 ALL_PERMISSIONS（= 不封顶），不是空集：未知/脏系统角色的行为因此与
# 今天完全一致（system_permissions 返回空集 → 结果仍是空集）。挡住脏值的是 DB CHECK
# 约束，不是这里；把默认写成空集会让「加了新系统角色忘了配封顶」变成静默的全站瘫痪。
SYSTEM_ROLE_CEILING: dict[str, frozenset[str]] = {
    "guest": frozenset({P_PROJECT_READ}),
}


def ceiling(system_role: str) -> frozenset[str]:
    """系统角色的权限天花板。未配置 = 不封顶。"""
    return SYSTEM_ROLE_CEILING.get(system_role, ALL_PERMISSIONS)


# 合法角色取值（供 DB CheckConstraint / Pydantic 校验共用同一份，避免两处漂移）。
#
# **「可写入的」和「匹配时认的」是两个集合，别合并**：
#   PROJECT_ROLES_ALL        = 允许写进库的取值 → DB CHECK + Pydantic。迁移
#                              zzx0role3 之后库里只可能有这两个值，两边必须一致，
#                              否则 create_all 建出来的约束比生产宽，测试会放过生产拒绝的写。
#   PROJECT_ROLES_RECOGNIZED = 读到旧值时仍认得的取值 → 归一/计数用。存量 token、
#                              未迁移的库、回滚过的环境都可能还带旧名。
#
# 注意 viewer / guest **两个旧只读名不在任何一边**：它们既不能再写入，
# 也**故意不做归一**（canonical 对未知名原样返回 → 守卫一律拒绝）。
# 把它们折进 member 会让残留的只读行悄悄拿到写权限 —— 代码先上、迁移后跑的那个窗口里
# 尤其危险。宁可让它们在窗口期什么都干不了（失败可见），也不要静默提权（失败不可见）。
SYSTEM_ROLES: tuple[str, ...] = ("admin", "user", "guest")
PROJECT_ROLES_ALL: tuple[str, ...] = ("manager", "member")
LEGACY_PROJECT_ROLES: tuple[str, ...] = ("project_admin", "developer", "tester")
PROJECT_ROLES_RECOGNIZED: tuple[str, ...] = PROJECT_ROLES_ALL + LEGACY_PROJECT_ROLES

# ── 端点守卫用的角色档位 ─────────────────────────────────────────
#
# 2 档模型下 TIER_DOC_MANAGE / TIER_WRITE / TIER_READ 的**取值完全相同** ——
# 这里**故意不合并成一个常量**。它们记的是「这个端点原本要求哪一档」，
# 是 PRD M2（把 require_project_role 换成 require_permission）唯一的现成依据：
# 合并掉，180 个端点就只剩一句 ("manager","member")，那份分档信息再也拿不回来
# （当初是靠通读 440 个端点才标出来的）。名字不同、取值相同，是有意的。
#
# 对应关系（左边是 2026-08-29 之前的守卫元组）：
#   ("project_admin",)                                     → TIER_ADMIN
#   ("project_admin","developer")                          → TIER_DOC_MANAGE
#   ("project_admin","developer","tester")                 → TIER_WRITE
#   ("project_admin","developer","tester","guest"|"viewer") → TIER_READ
TIER_ADMIN: tuple[str, ...] = ("manager",)
TIER_DOC_MANAGE: tuple[str, ...] = ("manager", "member")
TIER_WRITE: tuple[str, ...] = ("manager", "member")
TIER_READ: tuple[str, ...] = ("manager", "member")

# 项目角色归一：把新旧两套名折叠到同一个规范名，供 require_project_role 做等价匹配。
# 不做归一就会两头漏：存量端点用旧名、新端点用新名，同一个成员在两拨端点上表现不一致。
_CANONICAL_PROJECT_ROLE: dict[str, str] = {
    "manager": "manager", "project_admin": "manager",
    "member": "member", "developer": "member", "tester": "member",
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
    - 其余：(系统权限 ∪ 项目权限) ∩ 系统角色封顶。

    封顶是**减法**，永远只会让权限更少 —— 所以 guest 即便在项目里被挂成 manager，
    解析结果也不会超出 {project.read}。

    留接口：将来 user_permissions 单用户直授表，就在这里并上 user_overrides(user_id)，
    三处消费端不用改（直授也要过封顶，否则封顶就有了绕过路径）。
    """
    if system_role == "admin":
        return ALL_PERMISSIONS
    granted = system_permissions(system_role) | project_permissions(project_role)
    return granted & ceiling(system_role)


def has_permission(system_role: str, project_role: str | None, permission: str) -> bool:
    return permission in resolve_permissions(system_role, project_role)
