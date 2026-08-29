"""QA 对账 · 评审抽屉里 `DroppedNoAnchor` 的三档渲染，各截一张。

为什么要拦接口而不是造数据跑一趟真评审：三档里有一档是**存量结论**
（`droppedNoAnchor` 这个键压根不存在），那一档在真库里造不出来 ——
新代码只要跑过一次就会带上这个键。只有拦下响应把键删掉才看得见它。

跑法（要先起后端 8756 和前端）：

    LUMIERE_PROJ=<项目UUID> python3 backend/scripts/selftest/qa_dropped_no_anchor.py

项目得有至少一条 `done` 的域评审（页面上那一列点得开的徽标）。
"""
import json, os, pathlib, sys, tempfile
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from app.services.qa_catalog_review import VERDICT_CN  # noqa: E402

BASE = os.environ.get("LUMIERE_WEB", "http://127.0.0.1:5173")
PROJ = os.environ.get("LUMIERE_PROJ", "1a1fb724-e252-4fd2-a7f1-3bc6bfdc5cbe")
OUT = os.environ.get("LUMIERE_OUT", tempfile.gettempdir())

DROPPED = [
    {"scenario": "删除后越权访问", "why": "清单里没有这条", "dim": "coverage"},
    {"scenario": "并发撤销", "why": "清单没提", "dim": "coverage"},
]

CASES = [
    ("A_存量没这个键", None),
    ("B_查过了一条没丢", []),
    ("C_丢了两条", DROPPED),
]

results = []
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1500, "height": 1000})
    pg.goto(f"{BASE}/login", wait_until="networkidle")
    pg.fill('input[type="text"]', "admin")
    pg.fill('input[type="password"]', "admin123")
    pg.click('button[type="submit"]')
    pg.wait_for_url("**/projects", timeout=20000)

    state = {"dn": None, "hits": 0, "patched": 0}

    def walk(node):
        """响应外面套着 {"data": {"reviews": [...]}}，还可能再被全局信封包一层。
        与其猜形状，不如认对象：带 domain + result 的 dict 就是一条评审。"""
        if isinstance(node, list):
            for x in node:
                walk(x)
            return
        if not isinstance(node, dict):
            return
        res = node.get("result")
        if "domain" in node and isinstance(res, dict):
            res.pop("droppedNoAnchor", None)
            if state["dn"] is not None:
                res["droppedNoAnchor"] = state["dn"]
            state["patched"] += 1
        for v in node.values():
            walk(v)

    def handler(route):
        r = route.fetch()
        try:
            data = r.json()
        except Exception:
            return route.fulfill(response=r)
        walk(data)
        state["hits"] += 1
        route.fulfill(status=200, content_type="application/json",
                      body=json.dumps(data))

    pg.route("**/qa-catalog/reviews**", handler)

    for name, dn in CASES:
        state["dn"] = dn
        state["hits"] = 0
        state["patched"] = 0
        pg.goto(f"{BASE}/projects/{PROJ}/qa-catalog", wait_until="domcontentloaded")
        pg.wait_for_timeout(3500)
        # 评审结论标签只有三种文案，按文案定位 ——
        # antd v6 的 Tag 不把内联 style 透给 span，按 cursor:pointer 选不着。
        # **文案从 `VERDICT_CN` 现生成，不许抄一份进来。** 2026-08-29 换第五版措辞时，
        # 这里抄的那份是全仓唯一一处会**静默失效**的：三个词一个都匹配不上，
        # 脚本照常跑完、打一行「没找到标签」、退出码 0 —— 三张图一张没截，
        # 而它是「改完必须截图自测」那条规矩的执行者。**自测工具自己坏了不出声，
        # 等于把自测这件事悄悄取消了。**（前端那份 VERDICT 仍是各写一份，
        # 没有共同出处；两边不一致时以这条 py 为准 —— 导出的 Markdown 用的是它。）
        tag = pg.locator(
            "span.ant-tag:text-matches('%s')" % "|".join(VERDICT_CN.values())).first
        if not tag.count():
            print(f"!! {name}: 没找到可点开的评审标签")
            results.append((name, None, None))
            continue
        tag.click()
        pg.wait_for_timeout(1800)
        # 「清单本身漏了什么」在第二个页签里，第一个页签是给人看的结论。
        pg.locator(".ant-drawer-body .ant-tabs-tab", has_text="整改").first.click()
        pg.wait_for_timeout(1200)
        body = pg.locator(".ant-drawer-body").inner_text()
        has_hint = "没经过锚点检查" in body
        has_count = "指不出出处" in body
        # 截图要能看见判据本身那一行，滚到区块标题不够（它在屏幕外就等于没证据）
        anchor = ("指不出出处" if has_count else
                  "没经过锚点检查" if has_hint else "清单本身漏了什么")
        try:
            pg.locator(".ant-drawer-body").locator(f"text={anchor}").first.scroll_into_view_if_needed()
            pg.wait_for_timeout(500)
        except Exception as e:
            print(f"   （滚动失败：{str(e)[:50]}）")
        path = f"{OUT}/s81_fe_{name}.png"
        pg.screenshot(path=path)
        results.append((name, has_hint, has_count))
        print(f"{name}: 提示语={has_hint} 计数行={has_count} "
              f"(拦到{state['hits']}次/改了{state['patched']}条) -> {path}")
        pg.locator(".ant-drawer-close").first.click()
        pg.wait_for_timeout(400)
    b.close()

want = {"A_存量没这个键": (True, False), "B_查过了一条没丢": (False, False),
        "C_丢了两条": (False, True)}
bad = [r for r in results if r[1] is None or (r[1], r[2]) != want[r[0]]]
print("\n判据：A 只出提示语 / B 两样都不出 / C 只出计数行")
print("不符：", bad if bad else "无")
sys.exit(1 if bad else 0)
