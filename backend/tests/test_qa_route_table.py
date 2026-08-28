"""S7.2 路由表（对账的 R 侧）。

这一份的哨兵全都围着同一件事：**拉不到路由表的时候，报告上不能什么都没有。**
G2 的数字自己不会说话 —— 0 既可能是"这个域没有盲区"，也可能是"这一轮压根没查"，
而后者是个坏消息。所以每一条降级路径都必须留下那句声明。
"""
import httpx
import pytest

from app.services.qa_route_table import (
    G2_NOT_VERIFIED,
    G2_VERIFIED,
    ROUTES_PATH,
    fetch_route_table,
    parse_routes,
    route_table_note,
)

# 138 实测的形状（98 组 / 655 条），这里取三条代表
_REAL = {
    "groups": {
        "Health": [{"method": "GET", "path": "/healthz"}],
        "MCP-Tools": [{"method": "POST", "path": "/api/mcp/tools/{name}/call"},
                      {"method": "GET", "path": "/api/mcp/tools"}],
    }
}


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class Test拉取与降级:
    @pytest.mark.asyncio
    async def test_拉到了就有组有路由(self):
        async def h(request):
            assert request.url.path == ROUTES_PATH
            return httpx.Response(200, json=_REAL)

        async with _client(h) as c:
            t = await fetch_route_table("http://192.168.51.138:3000", client=c)
        assert t["available"] is True
        assert len(t["routes"]) == 3
        assert t["groups"] == ["Health", "MCP-Tools"]
        assert route_table_note(t)["g2"] == G2_VERIFIED

    @pytest.mark.asyncio
    async def test_桩404时结论里有声明且G2未验证(self):
        """AC 原文。**404 最容易被当成"这个系统没这个接口，那就算了"** ——
        算了之后 G2 恒为 0，报告上看不出这一类缺口根本没验过。
        （最常见的 404 其实是打错端口：网关 :8000 上没有这条路径。）
        """
        async def h(request):
            return httpx.Response(404, text="Not Found")

        async with _client(h) as c:
            t = await fetch_route_table("http://192.168.51.138:8000", client=c)
        assert t["available"] is False
        note = route_table_note(t)
        assert note["g2"] == G2_NOT_VERIFIED
        assert "本轮无路由表，G2 未验证" in note["declaration"]
        assert "404" in note["declaration"]

    @pytest.mark.asyncio
    async def test_连不上也要留声明不是静默跳过(self):
        """网络失败和 404 走的是两条代码路径，都得留声明 —— 只测 404 的话，
        超时那条路径可以静默 return 而测试全绿。"""
        async def h(request):
            raise httpx.ConnectError("connection refused")

        async with _client(h) as c:
            t = await fetch_route_table("http://192.168.51.138:3000", client=c)
        assert t["available"] is False
        assert "本轮无路由表，G2 未验证" in route_table_note(t)["declaration"]

    @pytest.mark.asyncio
    async def test_200但一条都读不出来不算成功(self):
        """**这种最像成功**：状态码 200、没抛异常、`routes` 是空列表。
        当成功处理的话，G2 会算成「路由表里一个端点都没有，所以没有盲区」——
        一个漂亮的绿。响应形状换了正是这个样子。"""
        async def h(request):
            return httpx.Response(200, json={"schemaVersion": 2, "items": None})

        async with _client(h) as c:
            t = await fetch_route_table("http://192.168.51.138:3000", client=c)
        assert t["available"] is False
        assert route_table_note(t)["g2"] == G2_NOT_VERIFIED

    @pytest.mark.asyncio
    async def test_没有BASE_URL不猜一个默认值(self):
        t = await fetch_route_table("")
        assert t["available"] is False
        assert "不猜" in t["reason"]
        assert t["url"] == ""

    @pytest.mark.asyncio
    async def test_真拉到的时候不许有那句声明(self):
        """降级规则的**反向锚点**：所有降级路径都留声明，很容易写成"永远留声明"，
        那样声明就成了噪声，页面上也就没人看了。"""
        async def h(request):
            return httpx.Response(200, json=_REAL)

        async with _client(h) as c:
            note = route_table_note(await fetch_route_table("http://x:3000", client=c))
        assert note["declaration"] == ""
        assert note["available"] is True


class Test形状容错:
    def test_顶层就是组名到列表(self):
        got = parse_routes({"Health": ["GET /healthz"]})
        assert got["routes"] == [{"group": "Health", "method": "GET", "path": "/healthz"}]

    def test_扁平列表带组字段(self):
        got = parse_routes({"routes": [{"tag": "Docs", "method": "get", "url": "/api/docs"}]})
        assert got["routes"] == [{"group": "Docs", "method": "GET", "path": "/api/docs"}]

    def test_认不出来的逐条记账不静默丢(self):
        """丢一条路由 = 少一个端点 = 少一类缺口，**且永远不会红**。
        接口是别人的，字段名我们说了不算 —— 所以认不出来要留痕，不能当没看见。"""
        got = parse_routes({"Health": [{"method": "GET"}, 42, {"path": "/ok"}]})
        assert [r["path"] for r in got["routes"]] == ["/ok"]
        assert len(got["unreadable"]) == 2
        assert all(u["group"] == "Health" for u in got["unreadable"])

    def test_组名底下不是列表也记账(self):
        got = parse_routes({"Health": "见另一份文档"})
        assert got["routes"] == []
        assert got["unreadable"][0]["group"] == "Health"

    def test_组名原样不归一化(self):
        """归一（大小写/单复数）是 S7.3 对账那一侧的事。在这里就归一，
        页面上再也看不出路由表原文写的是 `Tags` 还是 `Tag` ——
        而「组名改了写法」正是要看见的那个信号。"""
        got = parse_routes({"MCP-Tools": ["GET /a"], "health": ["GET /b"]})
        assert {r["group"] for r in got["routes"]} == {"MCP-Tools", "health"}

    def test_没有方法名的路由照收不当成读不出来(self):
        """只给路径不给方法的写法是存在的。把它算进 `unreadable`，
        就等于把一整个组的端点报成"读不出来"，然后这些端点在对账里凭空消失。"""
        got = parse_routes({"Docs": ["/api/docs"]})
        assert got["routes"] == [{"group": "Docs", "method": "", "path": "/api/docs"}]
        assert got["unreadable"] == []
