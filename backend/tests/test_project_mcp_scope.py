"""项目级 MCP 工具范围的封样。

范围从 Key 级挪到项目级之后，解析路径变成两条（项目 / 遗留），
最容易写错的就是**用什么判据在两条路之间选**。这里把那条判据钉死，
外加档位反查和路由的角色校验。
"""
import inspect

import pytest
from fastapi.routing import APIRoute

from app.api.mcp_keys import _match_profile, project_scope_router
from app.main import app
from app.mcp import TOOL_CATALOG
from app.mcp.profiles import PROFILES


PROJ = "11111111-1111-1111-1111-111111111111"
A, B = ["lum_list_projects"], ["lum_get_case", "lum_list_cases"]


@pytest.mark.parametrize("project_id,project_scope,legacy,expect,why", [
    (PROJ,  A,    B,    A,    "归属了项目 → 用项目范围，不看 Key 上那份"),
    (PROJ,  None, B,    None, "★项目明确设成不限制 → 就是不限制，不许掉回 Key 的旧范围"),
    (PROJ,  None, None, None, "都没设 → 不限制"),
    (None,  A,    B,    B,    "没归属项目 → 走遗留那条，存量 Key 行为不变"),
    (None,  A,    None, None, "没归属项目且 Key 自己没范围 → 不限制"),
    (PROJ,  [],   B,    None, "空列表和 NULL 一样当不限制（别让空数组静默锁死所有工具）"),
])
def test_解析判据是有没有归属项目(project_id, project_scope, legacy, expect, why):
    """`project_scope or legacy_scope` 是这里最自然也最错的写法 —— 第 2 行就是它会挂的地方。

    项目明确设成不限制时，那个写法会掉回 Key 上那份旧范围，
    等于把人刚放开的权限又悄悄收回去，页面上完全看不出为什么。
    """
    from app.mcp.middleware import pick_scope

    assert pick_scope(project_id, project_scope, legacy) == expect, why


def test_归属项目后立刻只认项目范围():
    """PATCH 把 Key 归到项目时，必须把 Key 上那份遗留范围一起清掉。

    留着的话就有两份来源：页面显示项目范围、实际生效的可能是另一份。
    """
    from app.api import mcp_keys

    src = inspect.getsource(mcp_keys.update_api_key)
    body = src[src.index("if body.project_id is not None:"):]
    assert "key.allowed_tools = None" in body.split("if body.reset_tools")[0]


def test_改项目范围要清缓存_而且是全清():
    """缓存按 key_hash 存，改的却是项目 —— 拿不到该项目所有 Key 的 hash 就只能全清。

    漏清的后果是"人在页面上改完、CC 那边还是旧范围"，最难查的那类问题。
    """
    from app.api import mcp_keys

    src = inspect.getsource(mcp_keys.set_project_scope)
    assert "invalidate_scope_cache()" in src, "改项目范围必须清缓存"


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


def test_写范围比读范围要求更高的角色():
    """读可以给到 guest，写不行 —— 改范围会影响别人正在用的连接。"""
    from app.api import mcp_keys

    read = inspect.getsource(mcp_keys.get_project_scope)
    write = inspect.getsource(mcp_keys.set_project_scope)
    assert "guest" in read
    assert "guest" not in write and "tester" not in write


def test_只写得进真存在的工具名():
    """拼错的工具名存进去，范围会静默变窄（那条工具永远不出现）。"""
    from app.api.mcp_keys import _validate_tools

    known = next(t["name"] for t in TOOL_CATALOG)
    assert _validate_tools([known, "lum_不存在的工具"]) == [known]
    assert _validate_tools(None) is None
