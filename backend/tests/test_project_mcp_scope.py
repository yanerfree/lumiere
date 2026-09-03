"""项目级 + Key 级 MCP 工具范围的封样。

生效范围 = **项目范围 ∩ Key 范围**（NULL = 该层不限）。最容易写错的两处
——「用 `or` 二选一」和「`[]` 当成不限制」——在这里钉死，
外加档位反查和路由的角色校验。

2026-09-03 之前判据是"有没有归属项目"：归属了就只看项目范围，Key 上那份整个
忽略。改成交集是因为一个项目里的两台 CC 常常各自只该看一小半工具（一台专做
回推、一台专做归因），而项目范围是共用的。
"""
import inspect

import pytest
from fastapi.routing import APIRoute

from app.api.mcp_keys import _match_profile, project_scope_router
from app.main import app
from app.mcp import TOOL_CATALOG
from app.mcp.middleware import blocked_by_project, pick_scope
from app.mcp.profiles import PROFILES


A = ["lum_list_projects"]
B = ["lum_get_case", "lum_list_cases"]
# A ⊂ AB，用来验"交集只收窄"：Key 勾的比项目给的多时，多出来的那些要被丢掉
AB = ["lum_list_projects", "lum_get_case"]


@pytest.mark.parametrize("project_scope,key_scope,expect,why", [
    (None, None, None, "两层都没设 → 不限制"),
    (A,    None, A,    "Key 跟随项目（NULL）→ 就是项目那份，也是今天所有 Key 的状态"),
    (None, B,    B,    "项目是天花板不限 → 生效就是 Key 那份，不许反过来变成不限制"),
    (AB,   A,    A,    "两层都设 → 交集"),
    (A,    AB,   A,    "★Key 勾了项目天花板外的 → 丢掉，不许反向扩出天花板"),
    (A,    B,    [],   "★交集为空就是空。这种 Key 连上来一个工具都没有，是真的没有"),
    (A,    [],   [],   "★`[]` 是「一个都不给」，不是「不限制」—— 方向反了还不报错"),
    (None, [],   [],   "★同上：项目不限也不能让空列表滑成不限制"),
])
def test_生效范围是两层的交集(project_scope, key_scope, expect, why):
    """`key_scope or project_scope`（或反过来）是这里最自然也最错的写法。

    `or` 是"二选一"，这里要的是"两个都得满足"。写成 `or` 时，Key 上勾了项目范围
    外的工具会让它**反向扩**出项目天花板 —— 范围是给人挑对工具用的，扩出去这道
    收窄就白做了；而且第 6、7 行那种空列表会直接被解析成"全都给"，方向完全反。
    """
    assert pick_scope(project_scope, key_scope) == expect, why


def test_被项目挡掉的工具要能报出来():
    """页面只显示"我勾了什么"是不够的：生效是交集，勾了天花板外的会被丢掉。

    不把丢掉的那几个说出来，人看到的就是"自己勾的东西莫名少了几个"，
    而少工具在 CC 那边只表现为"平台没有这个工具"。
    """
    assert blocked_by_project(A, AB) == ["lum_get_case"]
    assert blocked_by_project(A, A) == []
    # 任一层不限制 → 无所谓"被挡"
    assert blocked_by_project(None, AB) == []
    assert blocked_by_project(A, None) == []


def test_绑项目不再清掉Key那份收窄():
    """PATCH 把 Key 归到项目时，**不能**顺手把 Key 上那份范围清成 NULL。

    2026-09-03 之前那行 `key.allowed_tools = None` 是故意的，理由是"两份来源只
    留一个"。改成交集之后它的代价变成：换个项目就把人挑好的工具悄悄清空。
    一个来源的诉求现在由呈现解决（接口回生效范围 + 被挡掉的），不靠删数据。
    """
    from app.api import mcp_keys

    src = inspect.getsource(mcp_keys.update_api_key)
    # 只看代码行 —— 注释里会**提到**那行历史代码（说明为什么删掉的），
    # 连注释一起 grep 的话，把理由写清楚反而会让这条封样红。
    code = "\n".join(
        ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    body = code[code.index("if body.project_id is not None:"):]
    bind_branch = body.split("if body.reset_tools")[0]
    assert "key.allowed_tools = None" not in bind_branch, "绑项目不该清 Key 范围"
    # reset_tools 那条路仍然要能显式清成"跟随项目"
    assert "key.allowed_tools = None" in body


def test_接口把生效范围回给页面():
    """列表/新建/改 三个出口都得带 scope —— 少一个，那个页面就只能显示"我勾了什么"。"""
    from app.api import mcp_keys

    for fn in (mcp_keys.list_api_keys, mcp_keys.create_api_key, mcp_keys.update_api_key):
        src = inspect.getsource(fn)
        assert "_scope_views" in src, f"{fn.__name__} 没回生效范围"


def test_改项目范围要清缓存_而且是全清():
    """缓存按 key_hash 存，改的却是项目 —— 拿不到该项目所有 Key 的 hash 就只能全清。

    漏清的后果是"人在页面上改完、CC 那边还是旧范围"，最难查的那类问题。
    """
    from app.api import mcp_keys

    src = inspect.getsource(mcp_keys.set_project_scope)
    assert "invalidate_scope_cache()" in src, "改项目范围必须清缓存"


def test_改Key范围也要清缓存_而且只清这一把():
    """Key 级范围现在会真的生效，改完同样得让缓存失效。

    这里要的是**按 key_hash 清**：全清会把同时连着的其它 Key 一起打回查库，
    而改一把 Key 的范围本来只该影响它自己。
    """
    from app.api import mcp_keys

    src = inspect.getsource(mcp_keys.update_api_key)
    assert "invalidate_scope_cache(key.key_hash)" in src

def test_档位反查认得出每一档():
    """页面靠它把「当前生效」标出来。每个档位的工具列表都得能反查回自己。"""
    for p in PROFILES:
        assert _match_profile(p["tools"]) == p["key"], f"{p['key']} 反查不回来"


def test_不限制反查成全量_而不是custom():
    assert _match_profile(None) == "all"


def test_对不上任何一档就是custom():
    assert _match_profile(["lum_list_projects"]) == "custom"
    assert _match_profile([]) == "custom"


def test_落库的是工具名不是档位名():
    """存档位名的话，日后改了档位定义，已有项目的范围会**悄悄变**。

    `_match_profile` 只在读的时候反查用于展示 —— 它接收的必须是工具名列表。
    """
    src = inspect.getsource(_match_profile)
    assert "PROFILES" in src and "set(p[\"tools\"]) == cur" in src


@pytest.mark.parametrize("method", ["GET", "PUT"])
def test_项目范围路由挂了项目角色校验(method):
    """项目级资源不能只验"登录了"，否则 A 项目的人能改 B 项目的范围。"""
    routes = [r for r in app.routes
              if isinstance(r, APIRoute) and r.path == "/api/projects/{project_id}/mcp-scope"
              and method in r.methods]
    assert routes, f"{method} 路由没注册"
    names = set()

    def walk(dep):
        for sub in dep.dependencies:
            call = getattr(sub, "call", None)
            if call is not None:
                names.add(getattr(call, "__name__", ""))
            walk(sub)
    walk(routes[0].dependant)
    assert "_check" in names, f"{method} 少了 require_project_role"


def _guard_roles(method: str) -> set[str]:
    """把 mcp-scope 路由上 require_project_role 实际收的角色元组挖出来。

    比 grep 源码可靠：读的是依赖闭包里真正参与判定的那个 tuple，
    改成常量、换个写法、挪到别的行都不影响。
    """
    route = next(
        r for r in app.routes
        if isinstance(r, APIRoute)
        and r.path == "/api/projects/{project_id}/mcp-scope"
        and method in r.methods
    )
    found: set[str] = set()

    def walk(dep):
        for sub in dep.dependencies:
            call = getattr(sub, "call", None)
            if getattr(call, "__name__", "") == "_check" and getattr(call, "__closure__", None):
                for cell in call.__closure__:
                    v = cell.cell_contents
                    if isinstance(v, tuple) and v and all(isinstance(x, str) for x in v):
                        found.update(v)
            walk(sub)

    walk(route.dependant)
    return found


def test_写范围要求项目内写档_且游客够不着():
    """改范围会影响别人正在用的连接 —— 必须项目内写档，且游客一律打不进来。

    2026-08-29 之前这条比的是「读守卫里有 guest、写守卫里没有」。项目角色收敛成
    2 档后读写守卫取值相同，那个比法**变成了恒真的空断言**：两边都不含 "guest"。
    只读现在是账号属性，由 core/readonly_gate 的非 GET 闸门强制 —— 所以这里改成
    盯真正在起作用的两件事：档位挂对了没有、游客的 PUT 会不会被闸门拦下。
    """
    from app.core import permissions as perms
    from app.core import readonly_gate

    assert _guard_roles("GET") == set(perms.TIER_READ)
    assert _guard_roles("PUT") == set(perms.TIER_DOC_MANAGE)
    # 只读账号：PUT 被闸门拦死；GET 放行（读本来就该给，写不给）
    path = "/api/projects/{project_id}/mcp-scope"
    assert readonly_gate.blocks_guest("PUT", path)
    assert not readonly_gate.blocks_guest("GET", path)


def test_只写得进真存在的工具名():
    """拼错的工具名存进去，范围会静默变窄（那条工具永远不出现）。"""
    from app.api.mcp_keys import _validate_tools

    known = next(t["name"] for t in TOOL_CATALOG)
    assert _validate_tools([known, "lum_不存在的工具"]) == [known]
    assert _validate_tools(None) is None
