"""MCP Mock 多服务版的封样。

原来这个文件钉的是「一条坏配置不许打死整个服务」——单服务时代它只有一个形状
（工具配坏了整个 Mock 起不来）。改成多服务之后这件事变严重了：**一个服务里的
一个工具配坏，不许连累另一个服务**，因为另一个服务是另一个人在用。

另外三件事在这一版第一次有意义，一起钉上：
  · 参数是**有序列表**，不是 {名字: 类型} 的字典（顺序会原样进 tools/list 的
    schema，对面客户端按这个顺序给人填参）；
  · 三档松紧**真的不一样**（严格拦、宽松转、不校验放行），这是整个改造的卖点，
    退化了页面上看不出来 —— 三档都"能跑通"；
  · 认证失败必须是 **401 不是 403**（403 客户端不会去补认证头）。
"""
import json

from app.services.mcp_mock_manager import McpMockServerManager, _AuthGate
from app.services.mcp_mock_service import slug_error
from app.services.mcp_mock_validate import (
    build_schema,
    normalize_params,
    validate_arguments,
)


def _mgr() -> McpMockServerManager:
    m = McpMockServerManager.__new__(McpMockServerManager)   # 不走 __init__，不碰状态文件
    m.port = 28399
    m.host = "127.0.0.1"
    m._server = None
    m._task = None
    m._mounted = []
    return m


def _snap(slug, tools, *, validate_mode="strict", auth_type="none", auth_config=None):
    import uuid
    return {
        "id": uuid.uuid4(), "slug": slug, "name": slug, "instructions": "",
        "transport": "streamable-http", "auth_type": auth_type,
        "auth_config": auth_config, "validate_mode": validate_mode,
        "tools": [{"id": uuid.uuid4(), "name": n, "description": n,
                   "params": normalize_params(p)} for n, p in tools],
    }


# ── 参数归一 ──

def test_不填参数时params落成空列表而不是None():
    assert normalize_params(None) == []


def test_老的字典格式也收得住():
    """升级前存的是 {"branch_id": "string"}，读回来不能炸。"""
    out = normalize_params({"branch_id": "string", "page": "integer"})
    assert [p["name"] for p in out] == ["branch_id", "page"]
    assert out[1]["type"] == "integer"


def test_参数顺序原样保留():
    """顺序会进 tools/list 的 schema，对面客户端按这个顺序给人填参。

    用字典存的话顺序靠插入序撑着，存一趟数据库回来就乱了，**而且乱得没有报错**。
    """
    raw = [{"name": "z"}, {"name": "a"}, {"name": "m"}]
    assert [p["name"] for p in normalize_params(raw)] == ["z", "a", "m"]
    assert list(build_schema(raw)["properties"]) == ["z", "a", "m"]


def test_洗不出名字的参数丢掉而不是报错():
    assert normalize_params([{"type": "string"}, {"name": "ok"}, "整个不是字典"]) == \
        normalize_params([{"name": "ok"}])


def test_重名参数只留第一个():
    out = normalize_params([{"name": "q", "type": "string"}, {"name": "q", "type": "integer"}])
    assert len(out) == 1 and out[0]["type"] == "string"


# ── schema 是给对面看的 ──

def test_类型和必填真的写进schema():
    """老实现把每个参数都做成可选字符串，页面上标的 integer 纯属装饰。"""
    schema = build_schema([
        {"name": "page", "type": "integer", "required": True, "minimum": 1, "maximum": 100},
        {"name": "kind", "type": "string", "enum": "a,b,c"},
    ])
    assert schema["properties"]["page"]["type"] == "integer"
    assert schema["properties"]["page"]["minimum"] == 1
    assert schema["required"] == ["page"]
    assert schema["properties"]["kind"]["enum"] == ["a", "b", "c"]


def test_只有严格档封死多余参数():
    assert build_schema([{"name": "q"}], "strict")["additionalProperties"] is False
    assert "additionalProperties" not in build_schema([{"name": "q"}], "loose")
    assert "additionalProperties" not in build_schema([{"name": "q"}], "off")


# ── 三档松紧真的不一样 ──

_DEFS = [{"name": "page", "type": "integer", "required": True},
         {"name": "kind", "type": "string", "enum": ["a", "b"]}]


def test_严格档拦下缺必填和类型不符和多余参数():
    errs, _ = validate_arguments(_DEFS, {"kind": "z", "extra": 1}, "strict")
    joined = "；".join(errs)
    assert "page" in joined            # 缺必填
    assert "extra" in joined           # 多余参数
    assert "kind" in joined            # 不在枚举里


def test_严格档的报错一定带参数名():
    """不带名字的「参数校验失败」，对面只能挨个试 —— 比不校验还糟。"""
    errs, _ = validate_arguments(_DEFS, {"page": "3"}, "strict")
    assert errs and all("page" in e for e in errs)


def test_宽松档把能转的转掉():
    errs, args = validate_arguments(_DEFS, {"page": "3", "kind": "不在枚举里"}, "loose")
    assert errs == []
    assert args["page"] == 3           # "3" → 3
    assert args["kind"] == "不在枚举里"  # 枚举只放过不拦


def test_宽松档缺必填补零值而不是报错():
    errs, args = validate_arguments(_DEFS, {}, "loose")
    assert errs == [] and args["page"] == 0


def test_宽松档转不动的还是要报错():
    errs, _ = validate_arguments([{"name": "n", "type": "integer"}], {"n": "不是数字"}, "loose")
    assert errs and "n" in errs[0]


def test_不校验档一个字都不看():
    errs, args = validate_arguments(_DEFS, {"随便": "什么"}, "off")
    assert errs == [] and args == {"随便": "什么"}


def test_布尔不能混进integer():
    """Python 里 True 也是 int，不特判的话 {"page": true} 会被当成合法整数。"""
    errs, _ = validate_arguments([{"name": "page", "type": "integer"}], {"page": True}, "strict")
    assert errs


# ── 一条坏配置不许连累别人 ──

def test_一个工具配坏了不影响同服务的其余工具():
    m = _mgr()
    app = m._build_parent([_snap("s1", [
        ("good_one", [{"name": "q", "type": "string"}]),
        ("bad-name-with-dashes", [{"name": "1invalid"}, {"name": "class"}]),
    ])])
    assert app is not None
    assert m.mounted[0]["slug"] == "s1"


def test_一个服务配坏了不影响另一个服务():
    """多服务版新增的形状 —— 另一个服务是另一个人在用。"""
    m = _mgr()
    app = m._build_parent([
        _snap("broken", [("bad-name", [{"name": "class"}])]),
        _snap("healthy", [("ok_tool", [{"name": "q"}])]),
    ])
    assert app is not None
    assert {x["slug"] for x in m.mounted} == {"broken", "healthy"}


def test_没有服务时也起得来():
    """全都停用了不该是「起不来」，那样页面上只显示已停止、不说为什么。"""
    m = _mgr()
    assert m._build_parent([]) is not None
    assert m.mounted == []


def test_每个服务挂在自己的路径下():
    m = _mgr()
    m._build_parent([_snap("alpha", []), _snap("beta", [])])
    assert [x["path"] for x in m.mounted] == ["/alpha/mcp", "/beta/mcp"]


# ── 认证 ──

def _scope(headers: dict):
    return {"type": "http", "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()]}


def test_不认证的服务谁都能进():
    gate = _AuthGate(None, {"auth_type": "none"})
    assert gate._check(_scope({})) is None


def test_bearer缺头和错token都拦下来():
    gate = _AuthGate(None, {"auth_type": "bearer", "auth_config": {"token": "T"}})
    assert gate._check(_scope({})) is not None
    assert gate._check(_scope({"Authorization": "Basic T"})) is not None
    assert gate._check(_scope({"Authorization": "Bearer 错的"})) is not None
    assert gate._check(_scope({"Authorization": "Bearer T"})) is None


def test_apikey认自定义请求头名():
    gate = _AuthGate(None, {"auth_type": "apikey",
                            "auth_config": {"headerName": "X-My-Key", "apiKey": "K"}})
    assert gate._check(_scope({"X-API-Key": "K"})) is not None   # 头名不对
    assert gate._check(_scope({"X-My-Key": "K"})) is None


def test_没配token的服务一律拦住而不是放行():
    """配了认证却没填 key，最坏的实现是「没 key 就不比对」= 谁都能进。"""
    gate = _AuthGate(None, {"auth_type": "bearer", "auth_config": {}})
    assert gate._check(_scope({"Authorization": "Bearer 随便"})) is not None


def test_认证失败回401不是403():
    """403 会被客户端当成「认证对了但没权限」，于是不去补认证头 ——
    人看到的现象是「配了 token 也连不上」。"""
    sent = []

    class _Dummy:
        async def __call__(self, *a):  # pragma: no cover - 走不到
            raise AssertionError("不该放进来")

    gate = _AuthGate(_Dummy(), {"auth_type": "bearer", "auth_config": {"token": "T"}})

    async def send(msg):
        sent.append(msg)

    import asyncio
    asyncio.run(gate(_scope({}), None, send))
    assert sent[0]["status"] == 401
    assert any(k == b"www-authenticate" for k, _ in sent[0]["headers"])
    assert "Bearer Token" in json.loads(sent[1]["body"])["message"]


# ── 服务代号 ──

def test_服务代号的三种拒法都带原因():
    assert slug_error("") == "服务代号不能为空"
    assert slug_error("有中文") is not None
    assert slug_error("-abc") is not None
    assert slug_error("abc-") is not None
    assert slug_error("api") is not None       # 保留字，会和平台自己的路径撞
    assert slug_error("order-mock-1") is None
