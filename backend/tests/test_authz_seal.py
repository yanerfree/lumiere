"""M1 封样：项目级写端点的授权不变量 —— 结构级，锁全量路由。

这几条守的是「后来新增的页面/端点」最容易漏的两件事（正是本次大改的动机）：

1. **任何带 `{project_id}` 的写端点（POST/PUT/DELETE/PATCH）都必须挂 require_* 守卫。**
   只挂 `get_current_user`（登录即可）的写端点 = 任意登录用户越权改他人项目。
   本轮就是这么找出 knowledge/ai_config/mcp-scope 一批漏网的。

2. **只读角色（guest）不得触达会落库的写端点。** 语义按 FastAPI 的依赖 AND：
   一条路由可能同时挂「挂载级宽守卫（含 guest，为的是放行同前缀的 GET）」和
   「端点级严守卫（不含 guest）」，两者都要过 —— 所以 guest 被端点级挡住才算安全。
   只看「有没有一处 tuple 含 guest」会误报（documents 整组就是这么被误报的）。

直接读 app.routes 内省依赖闭包，不起服务、不连库、不碰 MCP 端口 —— 和截图那条封样同思路。
新增一个 guest 可达的写端点时，要么它其实不落库（加进 _GUEST_WRITE_ALLOWLIST 并写清理由），
要么就是个越权 bug，这条会红。
"""
from fastapi.routing import APIRoute

from app.main import app

_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}

# 允许 guest 到达的「写方法」端点：实为纯计算/预览，不落库，POST 只因要带 body。
# 每加一条都必须在这里写清「为什么它不是真的写」。
_GUEST_WRITE_ALLOWLIST = {
    # 模板展开预览：expand_template 纯函数，处理器连 session 都不注入，无任何持久化。
    ("POST", "/api/projects/{project_id}/branches/{branch_id}/cases/{case_id}/scenario-variables/preview"),
}


def _walk_guards(route):
    """收集一条路由整棵依赖树上的 require_project_role / require_role 信息。

    返回 (rpr_role_tuples, has_admin_only_role)：
    - rpr_role_tuples: 每个 require_project_role 守卫的项目角色元组
    - has_admin_only_role: 是否存在一个「非 admin 系统用户必然过不了」的 require_role 守卫
    """
    rpr_tuples = []
    has_admin_only = False

    def _roles_of(call):
        clo = call.__closure__ or []
        fv = dict(zip(call.__code__.co_freevars, [c.cell_contents for c in clo]))
        return fv.get("roles")

    def _walk(dep):
        nonlocal has_admin_only
        for sub in dep.dependencies:
            call = getattr(sub, "call", None)
            qn = getattr(call, "__qualname__", "") if call is not None else ""
            if qn.startswith("require_project_role"):
                rpr_tuples.append(_roles_of(call))
            elif qn.startswith("require_role"):
                roles = _roles_of(call) or ()
                # require_role("admin") 之类：系统普通用户（"user"）过不了 → 项目 guest 必然被挡
                if "user" not in roles:
                    has_admin_only = True
            _walk(sub)

    _walk(route.dependant)
    return rpr_tuples, has_admin_only


def _project_scoped_writes():
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        if "{project_id}" not in r.path:
            continue
        methods = r.methods & _WRITE_METHODS
        if not methods:
            continue
        yield r, methods


def test_all_project_scoped_writes_have_a_role_guard():
    """带 {project_id} 的写端点，绝不能只靠登录（get_current_user）把门。"""
    unguarded = []
    for route, methods in _project_scoped_writes():
        rpr, admin = _walk_guards(route)
        if not rpr and not admin:
            unguarded.append((sorted(methods), route.path))
    assert unguarded == [], f"以下项目级写端点缺少 require_* 守卫（登录即可越权）：\n{unguarded}"


def test_guest_cannot_reach_persisting_writes():
    """只读角色 guest 不得到达会落库的写端点（allowlist 里的纯预览除外）。"""
    offenders = []
    for route, methods in _project_scoped_writes():
        rpr, admin = _walk_guards(route)
        # guest 被挡：存在 admin-only 系统守卫，或存在某个不含 guest 的项目角色守卫
        guest_blocked = admin or any(rt is not None and "guest" not in rt for rt in rpr)
        if guest_blocked:
            continue
        for meth in sorted(methods):
            if (meth, route.path) not in _GUEST_WRITE_ALLOWLIST:
                offenders.append((meth, route.path))
    assert offenders == [], (
        "以下写端点 guest（只读角色）可达且不在白名单：\n"
        f"{offenders}\n若确为纯计算/不落库，请加进 _GUEST_WRITE_ALLOWLIST 并注明理由。"
    )
