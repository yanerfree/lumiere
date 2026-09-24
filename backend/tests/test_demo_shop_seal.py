"""订单服务 Demo 的封样：规则只能有一份，别让前端那份偷偷长歪。

这套被测系统的状态规矩写在后端 `demo_shop_service` 里，而页面上「这一步还能点
什么按钮」是前端 `DemoShop.jsx` 自己列的一张表。两张表一旦对不上，症状是**静默**的：
- 前端少一条 → 那个按钮不出现，功能看着像没做；
- 前端多一条 → 按钮点下去 409，看着像后端坏了。
两种都不报错，所以这里把两张表拿来逐字对一遍。

另外盯三件一改就出事的：端口不能撞上 28xxx 那段 mock、token 解析不许抛异常
（抛了就是 500 而不是 401）、迁移链不能断。
"""
import base64
import json
import re
import time
from pathlib import Path

import pytest

from app.services import demo_shop_catalog as catalog
from app.services import demo_shop_service as svc
from app.services.demo_shop_manager import (
    ACCOUNTS, DEFAULT_PORT, UNLOGGED_PATHS, _sign, demo_shop_manager,
    make_token, parse_token,
)

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "demo-shop" / "DemoShop.jsx"


# ───── 规则只有一份：页面不许自己抄一张表 ─────

def test_状态流转表是后端算出来发给页面的():
    """页面上那张「哪一步能点什么」是接口发过去的，不是前端另抄一份。

    抄一份的下场是**静默**的：抄少一条 → 那个动作看着像没做；抄多一条 → 点下去 409，
    看着像后端坏了。所以这里盯着两件事：算出来的表和后端规则逐条相等，
    以及前端**没有**偷偷留一张自己的表。
    """
    from app.api.demo_shop import list_endpoints
    import asyncio

    payload = asyncio.run(list_endpoints())
    got = {(t["action"], t["from"], t["to"]) for t in payload["rules"]["transitions"]}
    want = {(a, s, t) for a, table in svc.ALLOWED_TRANSITIONS.items()
            for s, t in table.items()}
    assert got == want, "发给页面的流转表和后端规则对不上"
    assert payload["rules"]["statusLabels"] == svc.STATUS_LABELS
    assert payload["rules"]["maxQuantity"] == svc.MAX_QUANTITY

    src = FRONTEND.read_text(encoding="utf-8")
    for name in ("NEXT_ACTIONS", "STATUS_META", "ALLOWED_TRANSITIONS"):
        assert f"const {name}" not in src, \
            f"页面又自己抄了一张 {name} —— 规则只能有一份，从 /demo-shop/endpoints 拿"


# ───── 端口 ─────

def test_端口没撞上mock那一段():
    """28xxx 整段留给各类 mock，订单服务是真系统，故意挪到 29000。"""
    assert DEFAULT_PORT == 29000
    assert not (28000 <= DEFAULT_PORT < 29000)


# ───── token：坏输入只能是 401，不能是 500 ─────

@pytest.mark.parametrize("bad", [
    "", "   ", "not-a-token", "a.b.c", "!!!!", "eyJhbGciOi", "x" * 500,
    "YWRtaW58YWRtaW58MXxzaWc=",          # 段数对但签名是假的
])
def test_伪造的token一律返回None不抛异常(bad):
    assert parse_token(bad) is None


def test_正常签出来的token解得回来():
    claims = parse_token(make_token("admin", "admin"))
    assert claims is not None
    assert claims["username"] == "admin"
    assert claims["role"] == "admin"


def test_把自己改成管理员签名就不认了():
    """最该防的一条：店员拿自己的 token 把 role 改成 admin 再打回来。

    ⚠ 别用「改 token 最后一个字符」来测这件事 —— base64 末位有几个比特是填充位，
    改了解出来还是同一串，测试会红在一个和签名无关的地方（2026-09-24 实际撞到）。
    要改就改解开后的内容，那才是真的伪造。
    """
    raw = base64.urlsafe_b64decode(make_token("clerk", "clerk") + "==").decode()
    forged = base64.urlsafe_b64encode(raw.replace("clerk|clerk", "clerk|admin").encode()).decode().rstrip("=")
    assert parse_token(forged) is None


def test_过期的token不认():
    """签名是对的，只是过了期 —— 这条要是漏了，token 等于永久有效。"""
    expired = f"admin|admin|{int(time.time()) - 60}"
    token = base64.urlsafe_b64encode(f"{expired}|{_sign(expired)}".encode()).decode().rstrip("=")
    assert parse_token(token) is None


def test_两个账号的权限不一样():
    """两个都是 admin 的话，403 那条分支在这套系统里就永远测不到。"""
    roles = {u: a["role"] for u, a in ACCOUNTS.items()}
    assert roles == {"admin": "admin", "clerk": "clerk"}


# ───── 样例数据 ─────

def test_样例商品里留着缺货和下架各一件():
    """没有这两件，「库存不足」「商品已下架」两条分支得先手工造数才测得到。"""
    assert any(p["stock"] == 0 for p in svc.SEED_PRODUCTS), "缺一件零库存商品"
    assert any(p.get("active", True) is False for p in svc.SEED_PRODUCTS), "缺一件已下架商品"
    skus = [p["sku"] for p in svc.SEED_PRODUCTS]
    assert len(skus) == len(set(skus)), "样例商品 sku 有重复"


def test_流转表里的动作和状态都是合法值():
    for action, table in svc.ALLOWED_TRANSITIONS.items():
        assert action in svc.ACTION_LABELS, f"{action} 没有中文名"
        for src_status, dst in table.items():
            assert src_status in svc.STATUSES, f"{action} 的起点 {src_status} 不是合法状态"
            assert dst in svc.STATUSES, f"{action} 的终点 {dst} 不是合法状态"


def test_终态不能再流转():
    reachable = {dst for t in svc.ALLOWED_TRANSITIONS.values() for dst in t.values()}
    for final in ("completed", "cancelled"):
        assert final in reachable, f"{final} 是个到不了的状态"
        assert all(final not in t for t in svc.ALLOWED_TRANSITIONS.values()), \
            f"{final} 还能继续流转，那它就不是终态"


# ───── 迁移链 ─────

def test_迁移接在当前链子上():
    mig = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "zzz7demoshop_demo_order_service.py"
    text = mig.read_text(encoding="utf-8")
    assert 'revision = "zzz7demoshop"' in text
    assert 'down_revision = "zzz6mockbi"' in text
    # 三张表都得有 downgrade，否则回滚会留下半套表
    for table in ("demo_products", "demo_orders", "demo_order_items"):
        assert f'"{table}"' in text.split("def downgrade")[1], f"{table} 没在 downgrade 里删掉"


def test_路由挂在测试工具权限下():
    """菜单在「测试工具」组里，接口也必须跟着那把锁 —— 否则菜单看不见、接口却能打。"""
    main = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert "app.include_router(demo_shop_router, dependencies=_TOOLS)" in main


def _real_routes() -> set[tuple[str, str]]:
    """真实跑起来的那个服务上注册了哪些 (方法, 路径)。"""
    app = demo_shop_manager._create_app()
    out: set[tuple[str, str]] = set()
    for r in app.routes:
        path = getattr(r, "path", None)
        methods = getattr(r, "methods", None)
        if not path or not methods or path in catalog.EXCLUDED_PATHS:
            continue
        for m in methods:
            if m in ("HEAD", "OPTIONS"):
                continue
            out.add((m, path))
    return out


def test_接口清单和真实路由一条不差():
    """页面左边那一列的唯一出处是 catalog，它和真实注册的路由必须双向相等。

    对不上是**静默**的：清单多一条 → 页面上摆着一条永远 404 的接口；
    清单少一条 → 那条接口谁也不知道它存在。两种都不报错，所以在这儿拦。
    """
    listed = {(e["method"], e["path"]) for e in catalog.ENDPOINTS}
    real = _real_routes()
    assert listed - real == set(), f"清单里有、服务上没有：{sorted(listed - real)}"
    assert real - listed == set(), f"服务上有、清单里漏了：{sorted(real - listed)}"


def test_接口清单每条都填全了():
    keys = {"key", "method", "path", "group", "name", "summary",
            "auth", "query", "pathParams", "body", "sampleResponse", "errors"}
    seen: set[str] = set()
    for e in catalog.ENDPOINTS:
        assert keys <= set(e), f"{e.get('path')} 少字段：{sorted(keys - set(e))}"
        assert e["auth"] in ("none", "user", "admin"), f"{e['path']} 的 auth 不是那三档"
        assert e["key"] not in seen, f"key 重了：{e['key']}"
        seen.add(e["key"])
        assert e["group"] in catalog.GROUP_ORDER, f"{e['path']} 的分组不在排序表里"
        # 路径里有 {x} 就必须在 pathParams 里给出样例值，否则页面上发出去是个字面量 {x}
        holes = set(re.findall(r"\{(\w+)\}", e["path"]))
        assert holes == {p["name"] for p in e["pathParams"]}, \
            f"{e['path']} 的路径参数对不上：{holes}"
        for p in e["pathParams"]:
            assert p["sample"], f"{e['path']} 的 {p['name']} 没给样例值"


def test_要登录的接口清单里都标了():
    """auth 标错了，页面上「用哪个账号」那一栏就会给错默认值，而请求照发不误。"""
    by_path = {(e["method"], e["path"]): e for e in catalog.ENDPOINTS}
    assert by_path[("GET", "/health")]["auth"] == "none"
    assert by_path[("POST", "/api/login")]["auth"] == "none"
    for k in (("DELETE", "/api/orders/{order_no}"),
              ("PATCH", "/api/products/{sku}"),
              ("POST", "/api/admin/reset")):
        assert by_path[k]["auth"] == "admin", f"{k} 应该是只有管理员能调"


def test_清单里的样例body是合法json():
    """页面把它直接塞进请求体输入框，写歪了用户点「发送」就是 422，还以为是接口坏了。"""
    for e in catalog.ENDPOINTS:
        for field in ("body", "sampleResponse"):
            raw = e.get(field)
            if raw:
                json.loads(raw)


def test_前端菜单和路由都接上了():
    app_jsx = FRONTEND.parents[2] / "App.jsx"
    src = app_jsx.read_text(encoding="utf-8")
    assert "'/tools/demo-shop'" in src, "菜单里没有订单服务"
    assert 'path="/tools/demo-shop"' in src, "路由没接，点菜单会白屏"
    i18n = FRONTEND.parents[2] / "utils" / "i18n.jsx"
    assert i18n.read_text(encoding="utf-8").count("'menu.demoShop'") == 2, "中英文菜单名要各有一条"


def test_不记日志的接口页面上会说明():
    """`/health` 故意不进请求日志，这件事必须发给页面。

    不发的下场：那一栏永远空着，看起来像「这条接口还没被调过」——
    于是有人去查一个根本不存在的 bug。名单只能有一份，中间件和页面用同一个常量。
    """
    from app.api.demo_shop import list_endpoints
    import asyncio

    payload = asyncio.run(list_endpoints())
    assert payload["rules"]["unloggedPaths"] == list(UNLOGGED_PATHS)
    assert "/health" in UNLOGGED_PATHS

    # 中间件必须用这个常量，不许再抄一份字面量
    src = (Path(__file__).resolve().parents[1] / "app" / "services"
           / "demo_shop_manager.py").read_text(encoding="utf-8")
    assert "if request.url.path in UNLOGGED_PATHS:" in src, \
        "中间件又写回字面量了 —— 改一处漏一处，页面上的说明就成了假话"
