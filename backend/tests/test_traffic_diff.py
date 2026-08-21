"""执行式审核 —— 「不能只停留在查看，而不真实执行」。

抓的是外部 CC 自己发现的那个错，也是这套东西存在的全部理由：
  页面打开订阅管理调的是 `/api/v1/subscriptions/provider-unified`，
  而它 22 条接口场景全用 `/api/v1/subscriptions/provider`。
  后者**存在、返回 200**，所以用例一直是绿的 —— 但页面根本不用它：
  `provider-unified` 坏掉、少给字段、跨租户条目漏掉，这批用例一条都不会红。

静态审核永远发现不了（两个 URL 都合法、都能通）。只有"真跑一遍 + 看页面到底发了
什么请求"能发现。所以 run_first 不只是"跑一下看红不红"，是**拿真实流量对账**。
"""
from __future__ import annotations

import inspect

from app.services.review.traffic_diff import compare, norm


def _req(path, method="GET", host="http://192.168.51.108:5176"):
    return {"url": f"{host}{path}", "method": method}


def _step(path, method="GET"):
    return {"url": "${BASE_URL}" + path, "method": method}


# ── 归一 ─────────────────────────────────────────────────────────

def test_归一把id段换掉():
    """不归一的话每次跑出来的 uuid 都不同，永远比不上。"""
    a = norm("http://x/api/v1/services/2f1a9c34-1111-2222-3333-444455556666", "GET")
    b = norm("http://x/api/v1/services/9999abcd-1111-2222-3333-444455556666", "GET")
    assert a == b == "GET /api/v1/services/{id}"


def test_归一带查询串也一样():
    assert norm("http://x/api/v1/subs?page=2", "GET") == norm("http://x/api/v1/subs", "GET")


def test_非接口请求不参与():
    for u in ("http://x/login", "http://x/assets/index.js", "http://x/api/auth/me"):
        assert norm(u, "GET") is None, u


# ── 核心：页面不调那个端点 ────────────────────────────────────────

def test_抓到接口场景用了页面不调的端点():
    facts = compare([_req("/api/v1/subscriptions/provider-unified")],
                    [_step("/api/v1/subscriptions/provider")], [], None)
    assert [f["kind"] for f in facts] == ["endpoint_not_used_by_page"]
    assert facts[0]["severity"] == "blocker", "这类是假绿的根源，不是「可以更好」"
    d = facts[0]["detail"]
    assert "provider" in d and "provider-unified" in d, "要把两边都列出来，否则改不动"


def test_前缀关系不能被兜底滤掉():
    """第一版加了「路径是某条流量的子串就算命中」想容忍差异，结果
    `/subscriptions/provider` 是 `/subscriptions/provider-unified` 的前缀 ——
    它把自己要抓的那个案例滤掉了。这条钉住别再加回来。"""
    src = inspect.getsource(compare)
    assert "not any(" not in src.split("ghosts")[1][:200], "又加 substring 兜底了"


def test_端点一致时不报():
    assert compare([_req("/api/v1/subscriptions/provider")],
                   [_step("/api/v1/subscriptions/provider")], [], None) == []


def test_没抓到流量就不下结论():
    """这次没跑 UI（或流量被回收了）时，不能拿"空流量"去判"接口场景全是幽灵"。"""
    assert compare([], [_step("/api/v1/anything")], [], None) == []


# ── 页面真调了但没验 ─────────────────────────────────────────────

def test_页面发的写请求没进接口场景要报():
    facts = compare([_req("/api/v1/services", "POST"), _req("/api/v1/services")],
                    [_step("/api/v1/services")], [], None)
    kinds = [f["kind"] for f in facts]
    assert "traffic_not_covered" in kinds
    assert [f for f in facts if f["kind"] == "traffic_not_covered"][0]["severity"] == "major"


def test_只读请求没进接口场景不报():
    """页面顺手拉的下拉框、字典这类 GET 不该逼人全验。"""
    facts = compare([_req("/api/v1/dicts"), _req("/api/v1/services")],
                    [_step("/api/v1/services")], [], None)
    assert "traffic_not_covered" not in [f["kind"] for f in facts]


# ── 步骤和脚本对不上 ─────────────────────────────────────────────

def test_脚本一个动作都没有算致命():
    facts = compare([_req("/api/v1/x")], [_step("/api/v1/x")],
                    [{"seq": 1, "action": "操作: 点发布"}, {"seq": 2, "action": "验证: 看状态"}],
                    "def test_x(page):\n    expect(page.get_by_text('x')).to_be_visible()\n")
    f = [x for x in facts if x["kind"] == "script_no_action"]
    assert f and f[0]["severity"] == "blocker"


def test_动作数明显少于步骤数报重要():
    script = "page.get_by_role('button').click()\n"
    facts = compare([_req("/api/v1/x")], [_step("/api/v1/x")],
                    [{"action": f"操作: 第 {i} 步"} for i in range(6)], script)
    assert "script_fewer_actions" in [x["kind"] for x in facts]


def test_前置清理步骤不算进对比():
    """前置和清理走接口（api fixture），页面上本来就没有对应动作。"""
    facts = compare([_req("/api/v1/x")], [_step("/api/v1/x")],
                    [{"action": "前置: 接口造服务"}, {"action": "清理: 接口删服务"}],
                    "page.get_by_role('button').click()\n")
    assert [x for x in facts if x["kind"].startswith("script_")] == []


# ── 接线 ─────────────────────────────────────────────────────────

def test_审核里真跑并对账():
    from app.services.review import reviewer
    src = inspect.getsource(reviewer._run_and_diff)
    assert "run_ui_script" in src, "UI 优先 —— 只有 UI 执行才有浏览器流量"
    assert "captured_requests" in src and "compare(" in src
    assert 'run_mode="debug"' in src, "审核跑不该进通过率口径"


def test_规范里把顺序写在最前面():
    from app.mcp.tools.sync import _SPEC_ORDER
    assert "先在页面上把这件事做一遍" in _SPEC_ORDER
    assert "tb_proxy_capture" in _SPEC_ORDER
    assert "先写 UI 脚本" in _SPEC_ORDER
    assert "provider-unified" in _SPEC_ORDER, "要把真实事故写进去，否则只是句口号"
    assert "fixture" in _SPEC_ORDER and "别在页面上点" in _SPEC_ORDER


def test_沙箱给了造数用的接口客户端():
    """前置走接口这件事，没有客户端就只是口号。"""
    import ast
    import tempfile
    from pathlib import Path

    from app.engine.pw_conftest import write_playwright_conftest
    d = tempfile.mkdtemp()
    write_playwright_conftest(d, env_vars={"BASE_URL": "http://x", "ADMIN_USERNAME": "a",
                                           "ADMIN_PASSWORD": "b"})
    src = Path(d, "conftest.py").read_text(encoding="utf-8")
    ast.parse(src)                      # 生成的脚本必须是合法 Python
    assert "def api()" in src and "class _Api" in src
    assert "不要用它验断言" in src, "得写清它是造数工具，不是断言工具"


def test_造数客户端支持多角色():
    """网关那种数据（平台建服务 → 租户申请 → 提供方审批）**一个 admin 造不出来**。
    这是用户点出来的：「如果需要多角色怎么办」。
    """
    import ast
    import tempfile
    from pathlib import Path

    from app.engine.pw_conftest import write_playwright_conftest
    d = tempfile.mkdtemp()
    write_playwright_conftest(d, env_vars={
        "BASE_URL": "http://gw", "ADMIN_USERNAME": "a", "ADMIN_PASSWORD": "1",
        "TENANT_USERNAME": "t", "TENANT_PASSWORD": "2",
        "PROVIDER_USERNAME": "p", "PROVIDER_PASSWORD": "3",
        "HALF_USERNAME": "只有用户名没有密码"})
    src = Path(d, "conftest.py").read_text(encoding="utf-8")
    ast.parse(src)
    roles_line = [l for l in src.splitlines() if l.startswith("ROLES")][0]
    for r in ("admin", "tenant", "provider"):
        assert f"'{r}'" in roles_line, f"角色 {r} 没被发现"
    assert "half" not in roles_line, "只有用户名没有密码的不该算一个角色"
    assert "def role(" in src and "def login(" in src, "换角色和临时账号都要有"
    assert "status_code == 401" in src, "token 过期要自动重登重试 —— 网关 15 分钟就失效"
    assert "已登记的是" in src, "角色名写错时要告诉它这个环境有哪些角色，别让它猜"


def test_路径变量归一不能多斜杠():
    """`/api/projects/${projId}` 曾被归一成 `/api/projects//{id}`，
    跟流量侧的 `/api/projects/{id}` 永远对不上 —— 于是**每一条带路径变量的步骤**
    都被当成"页面不调的幽灵端点"。活体验证时撞出来的。
    """
    from app.services.review.traffic_diff import _from_scenario, _from_traffic
    api = _from_scenario([{"method": "DELETE", "url": "${BASE_URL}/api/projects/${projId}"}])
    tr = _from_traffic([{"method": "DELETE",
                         "url": "http://x/api/projects/e278ada5-2812-4a87-813c-a7b0c0bfd5d5"}])
    assert list(api) == list(tr) == ["DELETE /api/projects/{id}"]
    assert not any("//" in k for k in api)


def test_合并后的结论保留kind():
    """丢了 kind 之后，前端要按类型筛、CC 要按类型判怎么改，都只能对文本做子串匹配。
    活体验证时我自己栽在这上面：探针按 kind 过滤永远是空，看起来像"没报"，其实报了。"""
    from app.services.review.reviewer import merge_findings
    out = merge_findings([{"kind": "endpoint_not_used_by_page", "severity": "blocker",
                           "where": "api", "detail": "端点对不上"}], [])
    assert out[0]["kind"] == "endpoint_not_used_by_page"


def test_没给环境不瞎跑():
    """env_id 为空时跑出来的是 BASE_URL="" 的垃圾运行（脚本导航到 "/login" 直接
    Protocol error），而审核会把它报成"这条跑挂了" —— 人会以为用例坏了。
    活体验证第一次就撞在这上面。"""
    import inspect

    from app.services.review import reviewer
    src = inspect.getsource(reviewer._run_and_diff)
    assert "review_run_skipped" in src and "_guess_env" in src
    assert "空环境跑出来的失败是假的" in src


def test_抓到多少条请求要露出来():
    """trafficSeen 是 0 的话，"没发现端点问题"只说明没得比，不说明端点是对的。"""
    import inspect

    from app.services.review import reviewer
    assert '"trafficSeen": ev.get("trafficSeen")' in inspect.getsource(reviewer.review_case)
