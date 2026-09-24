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
import re
import time
from pathlib import Path

import pytest

from app.services import demo_shop_service as svc
from app.services.demo_shop_manager import (
    ACCOUNTS, DEFAULT_PORT, _sign, make_token, parse_token,
)

FRONTEND = Path(__file__).resolve().parents[2] / "frontend" / "src" / "pages" / "demo-shop" / "DemoShop.jsx"


def _js_object(source: str, name: str) -> str:
    """从 jsx 里抠出 `const <name> = { ... }` 那一段（按花括号配对切，不用正则贪心）。"""
    start = source.index(f"const {name} = {{") + len(f"const {name} = ")
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError(f"{name} 在 {FRONTEND.name} 里没找到")


def _keys(block: str) -> list[str]:
    """取一层花括号内的顶层键名。"""
    out, depth = [], 0
    for line in block.splitlines():
        stripped = line.strip()
        if depth == 1:
            m = re.match(r"^([A-Za-z_][\w]*)\s*:", stripped)
            if m:
                out.append(m.group(1))
        depth += line.count("{") - line.count("}")
    return out


# ───── 前后端两张表必须一致 ─────

def test_页面上的状态标签和后端一字不差():
    src = FRONTEND.read_text(encoding="utf-8")
    block = _js_object(src, "STATUS_META")
    assert _keys(block) == svc.STATUSES, "前端 STATUS_META 的状态和后端 STATUSES 对不上"
    for status, label in svc.STATUS_LABELS.items():
        assert f"'{label}'" in block, f"前端缺状态「{label}」（{status}）的中文"


def test_页面上每个状态能点的按钮和后端允许的流转一致():
    src = FRONTEND.read_text(encoding="utf-8")
    block = _js_object(src, "NEXT_ACTIONS")
    assert _keys(block) == svc.STATUSES, "前端 NEXT_ACTIONS 少列或多列了状态"

    # 后端视角：每个状态实际允许哪几个动作
    expect = {s: set() for s in svc.STATUSES}
    for action, table in svc.ALLOWED_TRANSITIONS.items():
        for src_status in table:
            expect[src_status].add(action)

    # 前端视角：每个状态那一格里出现的 key
    actual = {}
    for status in svc.STATUSES:
        seg = block.split(f"{status}:", 1)[1]
        seg = seg.split("],", 1)[0] if "]" in seg.split("\n}", 1)[0] else seg
        actual[status] = set(re.findall(r"key:\s*'(\w+)'", seg.split("]")[0]))

    assert actual == expect, f"按钮表对不上：页面 {actual} vs 后端 {expect}"


def test_前端填数量的上限跟后端一致():
    src = FRONTEND.read_text(encoding="utf-8")
    assert f"max={{{svc.MAX_QUANTITY}}}" in src, "前端 InputNumber 的 max 和后端 MAX_QUANTITY 对不上"


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


def test_前端菜单和路由都接上了():
    app_jsx = FRONTEND.parents[2] / "App.jsx"
    src = app_jsx.read_text(encoding="utf-8")
    assert "'/tools/demo-shop'" in src, "菜单里没有订单服务"
    assert 'path="/tools/demo-shop"' in src, "路由没接，点菜单会白屏"
    i18n = FRONTEND.parents[2] / "utils" / "i18n.jsx"
    assert i18n.read_text(encoding="utf-8").count("'menu.demoShop'") == 2, "中英文菜单名要各有一条"
