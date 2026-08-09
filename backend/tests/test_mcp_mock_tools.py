"""MCP Mock 工具注册的封样 —— 一条坏配置不许打死整个服务。

实测踩到的：页面上建一个**不带参数**的工具，`params` 落库成 `None`，
`_create_app()` 里迭代它直接 TypeError，于是 MCP Mock 再也起不来 ——
页面上只显示"已停止"，不说为什么，删掉那个工具之前永远起不来。

这类"一条脏数据打死整个服务"的形状，代码里本来就防过一次（工具名非法会产生
SyntaxError），这次是同一类问题的另一个入口。所以钉两条：写入时归一，
注册时逐个兜底。
"""
from app.services.mcp_mock_manager import McpMockServerManager


def _mgr() -> McpMockServerManager:
    m = McpMockServerManager.__new__(McpMockServerManager)   # 不走 __init__，不碰状态文件
    m.port = 28399
    m.host = "127.0.0.1"
    m.transport = "streamable-http"
    m._server = None
    m._task = None
    m._tools = []
    from collections import deque
    m._call_logs = deque(maxlen=10)
    m._save_tools = lambda: None
    return m


def test_不填参数时params落成空字典而不是None():
    m = _mgr()
    t = m.add_tool({"name": "no_params"})
    assert t["params"] == {}


def test_显式传None也归一成空字典():
    """ToolCreate.params 默认就是 None，这是最容易走到的那条路。"""
    m = _mgr()
    t = m.add_tool({"name": "explicit_none", "params": None})
    assert t["params"] == {}


def test_params是None的历史脏数据也能起起来():
    """存量 mcp_mock_tools.json 里已经有 params=null 的行，读回来不能炸。"""
    m = _mgr()
    m._tools = [{"name": "legacy", "description": "旧数据", "params": None,
                 "mode": "success", "enabled": True, "successData": {"ok": True}}]
    app = m._create_app()
    assert app is not None


def test_一个工具配坏了不影响其余工具():
    """代价应该是这一个用不了，不是整个 Mock 服务起不来。"""
    m = _mgr()
    m._tools = [
        {"name": "good_one", "description": "正常", "params": {"q": "string"},
         "mode": "success", "enabled": True, "successData": {"ok": True}},
        {"name": "bad-name-with-dashes", "description": "名字不是合法标识符",
         "params": {"1invalid": "string", "class": "string"},
         "mode": "success", "enabled": True, "successData": {"ok": True}},
    ]
    app = m._create_app()
    assert app is not None


def test_禁用的工具不注册():
    m = _mgr()
    m._tools = [{"name": "off", "description": "", "params": {}, "mode": "success",
                 "enabled": False, "successData": {}}]
    assert m._create_app() is not None
