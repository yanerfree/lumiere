"""M1 封样：写端点的授权不变量 —— 结构级，锁全量路由。

两条互相正交的不变量，都是「后来新增的页面/端点」最容易漏的：

1. **任何带 `{project_id}` 的写端点都必须挂 require_* 守卫。**
   只挂 `get_current_user`（登录即可）的写端点 = 任意登录用户越权改他人项目。
   管的是「非成员/低档成员越权」。

2. **游客（系统角色 guest）打不到任何会落库的写端点。**
   管的是「账号级只读封顶」。

⚠ 这一条 2026-08-29 改写过，改写的原因值得记下来：原文断言的主语是**项目角色 guest**
（遍历带 `{project_id}` 的写路由，看守卫元组里有没有 "guest"）。角色模型收敛成
manager/member 两档之后，项目角色 guest 不存在了 —— 于是「没有任何守卫元组含 guest」
恒成立，这条封样**跑绿但什么也没验**，而且绿得毫无异样。恒真的封样比没有封样更坏：
它占着"这件事有人看着"的位置。

新主语是 `core.readonly_gate`：闸门是纯函数，所以这里离线遍历整张路由表就能验，
不起服务、不连库、不碰 MCP 端口。新增一个游客可达的写端点时，要么它其实不落库
（加进 `readonly_gate.GUEST_WRITE_ALLOWLIST` 并写清理由），要么就是个越权 bug，这条会红。
"""
from fastapi.routing import APIRoute

from app.core import readonly_gate
from app.main import app

_WRITE_METHODS = {"POST", "PUT", "DELETE", "PATCH"}


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
                # require_role("admin") 之类：系统普通用户（"user"）过不了
                if "user" not in roles:
                    has_admin_only = True
            _walk(sub)

    _walk(route.dependant)
    return rpr_tuples, has_admin_only


def _api_routes():
    for r in app.routes:
        if isinstance(r, APIRoute) and r.path.startswith("/api"):
            yield r


def _project_scoped_writes():
    for r in _api_routes():
        if "{project_id}" not in r.path:
            continue
        methods = r.methods & _WRITE_METHODS
        if methods:
            yield r, methods


def _all_writes():
    """整个 /api 下的非安全方法路由 —— 注意这是全量，不限 {project_id}。

    正是这个「全量」把腿 A 和腿 B 区分开：129 条写路由压根没有项目语境，
    按权限点/项目角色去挡的形状覆盖不到它们。
    """
    for r in _api_routes():
        for meth in sorted(r.methods):
            if meth.upper() not in readonly_gate.SAFE_METHODS:
                yield meth, r.path


def test_all_project_scoped_writes_have_a_role_guard():
    """带 {project_id} 的写端点，绝不能只靠登录（get_current_user）把门。"""
    unguarded = []
    for route, methods in _project_scoped_writes():
        rpr, admin = _walk_guards(route)
        if not rpr and not admin:
            unguarded.append((sorted(methods), route.path))
    assert unguarded == [], f"以下项目级写端点缺少 require_* 守卫（登录即可越权）：\n{unguarded}"


# 游客实际打得到的写端点全集 —— **逐条列死在这里**。
# 不是"从白名单推出来"（那样是拿被测物证明自己，恒真），而是从**真实路由表**
# 与闸门相交算出来，再和这份人工清单对齐：白名单加一条、或某条端点改了路径，
# 都会让两边对不上而变红，逼一次人工过目。
_SEALED_GUEST_REACHABLE_WRITES = {
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/refresh"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/change-password"),
    ("POST", "/api/assistant/chat"),
    (
        "POST",
        "/api/projects/{project_id}/branches/{branch_id}/cases/{case_id}"
        "/scenario-variables/preview",
    ),
}


def test_the_set_of_guest_reachable_writes_is_sealed():
    """游客能打到的写端点，只能是上面列死的那 6 条。

    ⚠ 这条的形状是刻意的。最自然的写法——「遍历写路由，断言 `not blocks_guest(...)`
    的都在白名单里」——**是个恒真式**：`blocks_guest` 对非安全方法的定义就是
    `path not in GUEST_WRITE_ALLOWLIST`，两边同源，永远不可能有反例。写成那样等于
    没测（本文件 2026-08-29 第一版就是那样，当场发现并改掉）。
    这里改成与**独立的一份人工清单**比对，才有反例可言。
    """
    actual = {
        (meth, path) for meth, path in _all_writes() if not readonly_gate.blocks_guest(meth, path)
    }
    extra = actual - _SEALED_GUEST_REACHABLE_WRITES
    missing = _SEALED_GUEST_REACHABLE_WRITES - actual
    assert not extra, (
        f"游客新拿到了这些写端点：{sorted(extra)}\n"
        "若确为纯计算/不落库，补进 _SEALED_GUEST_REACHABLE_WRITES 并在 readonly_gate 里写清理由。"
    )
    assert not missing, (
        f"这些端点不再对游客开放（或路径变了）：{sorted(missing)}\n"
        "白名单里对应的条目已经形同虚设，一并清掉。"
    )


def test_guest_write_allowlist_entries_are_real_and_justified():
    """白名单纪律：每条必须①有非空理由②对应真实存在的写路由。

    第②点是防「白名单被塞」的关键 —— 光有理由挡不住往里加条目，但一条**指不到
    任何真实路由**的白名单项要么是笔误（真正想放行的那条其实还被挡着、且没人发现），
    要么是给将来偷偷开的口子。两种都该当场红。
    """
    real_write_paths = {path for _, path in _all_writes()}

    for path, reason in readonly_gate.GUEST_WRITE_ALLOWLIST.items():
        assert reason and reason.strip(), f"白名单条目 {path} 没写理由"
        assert path in real_write_paths, (
            f"白名单条目 {path} 指不到任何真实的写路由 —— 路径模板写错了，"
            "或者对应端点已被删除；这条白名单现在什么也没放行，属于死条目"
        )


def test_the_gate_actually_blocks_the_things_it_must_block():
    """反向断言：拿几条**必须挡住**的代表性路由验闸门真的会返回 True。

    没有这条的话，`blocks_guest` 被改成 `return False` 时上面几条会全绿
    （offenders 空、白名单条目仍真实）—— 又是一次恒真。
    """
    must_block = [
        # 助手真正执行提案的那条：形状和 /chat 一样是 POST，但它落库，故意不在白名单
        ("POST", "/api/assistant/execute"),
        # 建项目：游客的系统权限为空集，这条在闸门和权限点两处都该拒
        ("POST", "/api/projects"),
        # 没有项目语境的一条 mock 写 —— 代表那 129 条只有闸门管得着的路由
        ("POST", "/api/llm-mock/routes"),
    ]
    real = {(m, p) for m, p in _all_writes()}
    for meth, path in must_block:
        assert (meth, path) in real, f"{meth} {path} 不再是真实路由，这条断言已失去主语"
        assert readonly_gate.blocks_guest(meth, path), f"闸门竟然放行了 {meth} {path}"

    # 安全方法一律不挡（游客要能读）
    assert not readonly_gate.blocks_guest("GET", "/api/projects")
    assert not readonly_gate.blocks_guest("HEAD", "/api/projects")


def test_guest_gate_is_wired_into_get_current_user():
    """闸门必须真的挂在鉴权链上 —— 纯函数写得再对，没人调就等于没有。

    这是 operator 那个空壳错误的同款防线：自报一套、强制另一套。
    """
    import inspect

    from app.deps import auth

    src = inspect.getsource(auth.get_current_user)
    assert "_enforce_guest_readonly" in src, "get_current_user 没有调用游客闸门"

    gate_src = inspect.getsource(auth._enforce_guest_readonly)
    assert "readonly_gate.blocks_guest" in gate_src
    # 取不到 route 时必须 fail-closed，不能默认放行
    assert "SAFE_METHODS" in gate_src
