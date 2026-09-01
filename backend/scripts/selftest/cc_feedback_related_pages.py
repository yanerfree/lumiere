"""CC 反馈通道 · **相关页面**回归走查（只读，不写库）。

为什么单独有这一份 —— 加一条通道从来不只动它自己那一页。这次动到的是
**别人的页面**：
  · 操作日志页有一份**手写的**对象类型清单（`TARGET_TYPES` / `TARGET_TYPE_LABELS`），
    而 `cc_feedback_service` 会往 `audit_logs` 写 `target_type='cc_feedback'`。
    两处漏任何一处都**不报错**：漏 LABELS 就在页面上露出 `cc_feedback` 这串原始码，
    漏 TARGET_TYPES 则筛选下拉里根本选不到它 —— 那类记录看着像"没记账"。
  · MCP 工具页给工具分组配色，新分组「平台反馈」用了自定义色名。
  · AI 能力→模型页按 `ai_capabilities.py` 的清单铺档位，新加一档不能变成
    绑不上模型的空档。

这三件事单测都盯不住（它们是前端手写常量和渲染），只有真开浏览器才看得见。

跑法（先起后端 + 一个指向它的 vite）：

    LUMIERE_WEB=http://127.0.0.1:5188 LUMIERE_PROJECT=<projectId> \
      backend/.venv/bin/python backend/scripts/selftest/cc_feedback_related_pages.py

**全程只读**：只翻页、只筛选，不新建不处置 —— 所以可以直接对着开发库跑，
不会在别人的操作日志里留下自测垃圾。退出码 0 = 全过。
"""
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright

BASE = os.environ.get("LUMIERE_WEB", "http://127.0.0.1:5188")
OUT = os.environ.get("LUMIERE_OUT", "/tmp")
PROJECT = os.environ.get("LUMIERE_PROJECT", "")
ADMIN_PW = os.environ.get("LUMIERE_ADMIN_PASSWORD", "admin123")

fails: list[str] = []


def ck(cond: bool, ok: str, bad: str) -> bool:
    print(("   ✓ " + ok) if cond else ("   ✗ " + bad))
    if not cond:
        fails.append(bad)
    return cond


def shot(pg, name: str) -> None:
    p = str(pathlib.Path(OUT) / name)
    pg.screenshot(path=p, full_page=False)
    print(f"   截图 {p}")


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 1100})
        errs: list[str] = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        # 光看控制台只能看到一句"403 Forbidden"，看不出是**哪个接口** ——
        # 而这条通道新加了权限点，403 打在谁身上是必须分清的。
        bad_req: list[str] = []
        pg.on("response", lambda r: bad_req.append(f"{r.status} {r.request.method} {r.url}")
              if r.status >= 400 else None)

        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[type="text"]', "admin")
        pg.fill('input[type="password"]', ADMIN_PW)
        pg.click('button[type="submit"]')
        pg.wait_for_url("**/projects", timeout=20000)

        # ── 1. 操作日志页 ─────────────────────────────────────────
        print("== 操作日志 ==")
        pg.goto(f"{BASE}/settings/logs", wait_until="domcontentloaded")
        pg.wait_for_timeout(2500)
        rows = pg.locator(".ant-table-tbody tr.ant-table-row")
        ck(rows.count() > 0, f"日志列出 {rows.count()} 行", "日志表是空的")

        # 下拉里有没有「CC 反馈」这一项 —— 没有的话那类记录筛不出来
        selects = pg.locator(".ant-select")
        opened = False
        for i in range(selects.count()):
            s = selects.nth(i)
            if "对象类型" in (s.inner_text() or "") or \
               "对象类型" in (s.get_attribute("title") or ""):
                s.click()
                opened = True
                break
        if not opened:
            # 兜底：按占位符文案找
            ph = pg.locator("input[placeholder*='对象类型'], .ant-select:has-text('对象类型')")
            if ph.count():
                ph.first.click()
                opened = True
        ck(opened, "找到「对象类型」筛选", "页面上没有「对象类型」筛选")
        pg.wait_for_timeout(600)
        opts = pg.locator(".ant-select-item-option")
        texts = [opts.nth(i).inner_text() for i in range(opts.count())]
        print(f"   对象类型选项：{texts}")
        ck("CC 反馈" in texts, "下拉里有「CC 反馈」",
           "下拉里没有「CC 反馈」—— TARGET_TYPES 漏了 cc_feedback")

        # 选中它，看筛出来的行长什么样
        if "CC 反馈" in texts:
            opts.nth(texts.index("CC 反馈")).click()
            pg.wait_for_timeout(2000)
            rows = pg.locator(".ant-table-tbody tr.ant-table-row")
            n = rows.count()
            ck(n > 0, f"筛出 {n} 行 CC 反馈记录",
               "筛不出记录 —— 要么没写审计，要么筛选值对不上")
            body = pg.locator(".ant-table-tbody").inner_text()
            ck("CC 反馈" in body, "行里显示中文标签「CC 反馈」",
               "行里没有中文标签")
            ck("cc_feedback" not in body, "没露出 cc_feedback 原始码",
               "页面上露出了 cc_feedback 原始码 —— TARGET_TYPE_LABELS 漏了")
            shot(pg, "rel_audit_logs.png")

        # ── 2. MCP 工具页 ────────────────────────────────────────
        if PROJECT:
            print("== MCP 工具页 ==")
            pg.goto(f"{BASE}/projects/{PROJECT}/settings/mcp-tools",
                    wait_until="domcontentloaded")
            pg.wait_for_timeout(2500)
            # 工具清单在「工具范围」页签下，默认停在「连接管理」——
            # 不点这一下就只能看到一张"本项目还没有连接"的空态，
            # 而那和"工具没注册"在 body 文本上长得一模一样。
            tab = pg.locator(".ant-tabs-tab", has_text="工具范围")
            ck(tab.count() > 0, "有「工具范围」页签", "没有「工具范围」页签")
            if tab.count():
                tab.first.click()
                pg.wait_for_timeout(1500)
            # 分类清单折在「查看工具明细」后面 —— 页签本身只列"活"（档位）。
            more = pg.locator("text=查看工具明细")
            if more.count():
                more.first.click()
                pg.wait_for_timeout(1200)
            # 工具名还折在分类里，再点一下「全部展开」才看得到。
            exp = pg.locator("text=全部展开")
            if exp.count():
                exp.first.click()
                pg.wait_for_timeout(1200)
            txt = pg.locator("body").inner_text()
            ck("平台反馈" in txt, "有「平台反馈」分组",
               "没有「平台反馈」分组 —— 工具没注册或分类名对不上")
            ck("lum_report_feedback" in txt, "列出了 lum_report_feedback",
               "没列出 lum_report_feedback")
            shot(pg, "rel_mcp_tools.png")

            # ── 3. AI 能力→模型 ──────────────────────────────────
            print("== AI 能力→模型 ==")
            pg.goto(f"{BASE}/projects/{PROJECT}/settings/ai-capabilities",
                    wait_until="domcontentloaded")
            pg.wait_for_timeout(2500)
            txt = pg.locator("body").inner_text()
            ck("CC 反馈分诊" in txt, "有「CC 反馈分诊」档位",
               "没有「CC 反馈分诊」档位")
            # 档位那一行必须能落到一个模型上（空档位是这一页的典型坏法）
            row = pg.locator("tr:has-text('CC 反馈分诊')")
            if row.count():
                rtxt = row.first.inner_text()
                print(f"   档位行：{rtxt!r}")
                ck(len(rtxt.strip()) > len("CC 反馈分诊") + 4,
                   "档位行不是空的", "档位行是空的 —— 绑不上模型")
            shot(pg, "rel_ai_capabilities.png")
        else:
            print("!! 没给 LUMIERE_PROJECT，跳过项目内两页")

        # ── 4. CC 反馈页本身还在 ─────────────────────────────────
        print("== CC 反馈页 ==")
        pg.goto(f"{BASE}/settings/cc-feedback", wait_until="domcontentloaded")
        pg.wait_for_timeout(2500)
        rows = pg.locator(".ant-table-tbody tr.ant-table-row")
        ck(rows.count() > 0, f"列出 {rows.count()} 行", "表是空的")
        shot(pg, "rel_cc_feedback.png")

        # 控制台报错：React 的 key 冲突/未定义渲染都会在这里露头。
        #
        # 两类要摘掉，但**摘的理由不一样，别混成一句"忽略告警"**：
        #  · `Warning: [antd: X] ... is deprecated` —— 全站存量。antd v6 改了一批
        #    属性名（Drawer 的 width、Space 的 split、Collapse 的 expandIconPosition、
        #    message 静态方法…），这次改动一个都没引入：改动只在 MCPTools 的两张
        #    颜色表里加了两行。要治是**单独一件事**，只把我这一页改成新写法反而
        #    和邻居不一致。
        #  · 字体 403 —— **这套临时栈自己的**：worktree 的 node_modules 是指向主
        #    checkout 的软链，vite 的 fs.allow 不覆盖那条真实路径。用户日常那套
        #    5173 在主 checkout 里跑，没这回事。
        depr = [e for e in errs if e.startswith("Warning: [antd:") and "deprecated" in e]
        envnoise = [e for e in errs if "Failed to load resource" in e or "favicon" in e.lower()]
        ctxwarn = [e for e in errs if "Static function can not consume context" in e]
        noisy = [e for e in errs if e not in depr and e not in envnoise and e not in ctxwarn]
        print(f"   控制台 {len(errs)} 条 = 存量弃用告警 {len(depr)} + "
              f"message 静态方法 {len(ctxwarn)} + 临时栈字体 403 {len(envnoise)} + "
              f"其它 {len(noisy)}")
        for e in sorted(set(depr)):
            print("     [存量] " + e[:110])
        ck(not noisy, "没有新的控制台报错", f"控制台报错：{noisy[:3]}")

        # 4xx/5xx 请求：允许存量的（比如未配置的服务探活），但要打印出来看清楚
        if bad_req:
            print("   4xx/5xx 请求：")
            for r in sorted(set(bad_req)):
                print("     " + r)
        hit = [r for r in bad_req if "/api/cc-feedback" in r or "/api/logs" in r]
        ck(not hit, "本次涉及的接口没有 4xx/5xx", f"CC 反馈/日志接口报错：{hit[:3]}")

        b.close()

    print("\n" + ("✓ 全过" if not fails else f"✗ {len(fails)} 处没过"))
    for f in fails:
        print("   - " + f)
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
