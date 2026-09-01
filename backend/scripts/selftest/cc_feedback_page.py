"""CC 反馈页 · 活体自测：**从人的角度**把这一页走一遍。

为什么非要有这一份 —— 2026-09-01 这一版接口层漏了 `{"data": ...}` 那层壳。
后端 200、前端 `res.data.items` 拿到 undefined，页面渲染成一张空表：
**看着像「CC 还没报过问题」，而不是「接错了」**。单测全绿，没有一条会红。
所以这里咬的不是接口，是**页面上到底有没有东西**。

同样只有真开浏览器才验得了的第二件事：处置被拒时那句「为什么 + 该怎么改」
有没有真的到人眼前。后端把它折进 `AppError.detail`，中间要经过
`utils/request.js` 挂到 `err.detail`、页面再拼进 toast —— 这条链断在哪一节
都不报错，只是 toast 变成一句没用的「提交失败」。

跑法（先起后端 8756 + 一个前端 vite）：

    LUMIERE_WEB=http://127.0.0.1:5188 \
      backend/.venv/bin/python backend/scripts/selftest/cc_feedback_page.py

**退出码有意义**：0 = 全过，1 = 有一步没过。
自测数据自己收尾：标题带 `自测·` 前缀的行跑完就删（不删的话，第二趟会撞上
「不需要处理」的指纹短路，走出一条和第一趟完全不同的路 —— 而那种不稳定
最难查）。
"""
import os
import pathlib
import subprocess
import sys
import time

from playwright.sync_api import sync_playwright

# antd v6 改了一批内部类名，v5 的写法在这里是**找不到元素**而不是报错：
#   .ant-select-selection-item → .ant-select-content-has-value
#   .ant-drawer-content        → .ant-drawer-section / .ant-drawer-body
#   .ant-modal-content         → .ant-modal-container
# 没变的：.ant-table-tbody tr.ant-table-row、.ant-select-item-option、
#         .ant-modal-footer、.ant-message-notice、.ant-form-item-explain-error

BASE = os.environ.get("LUMIERE_WEB", "http://127.0.0.1:5188")
OUT = os.environ.get("LUMIERE_OUT", "/tmp")
# 收尾用的库名。默认是开发库；跑在别的库上（比如临时起的一套）时用它改，
# 否则自测行删到了**另一个库**上 —— 当场不报错，第二趟才炸。
DB = os.environ.get("LUMIERE_DB", "lumiere")
ADMIN_PW = os.environ.get("LUMIERE_ADMIN_PASSWORD", "admin123")
MARK = "自测·"
STAMP = time.strftime("%H%M%S")

fails: list[str] = []


def ck(cond: bool, ok: str, bad: str) -> bool:
    print(("   ✓ " + ok) if cond else ("   ✗ " + bad))
    if not cond:
        fails.append(bad)
    return cond


def cleanup() -> None:
    """删掉自测行。走 psql 而不是页面 —— 页面上根本没有「删除」这个动作，
    而这条通道的设计就是**不给删**（删掉等于把回音和复发计数一起抹了）。"""
    sql = f"delete from cc_feedback where title like '{MARK}%'"
    subprocess.run(["psql", "-h", "localhost", "-U", "postgres", "-d", DB,
                    "-tAc", sql],
                   env={**os.environ, "PGPASSWORD": "postgres"},
                   capture_output=True, check=False)


def toast_text(pg) -> str:
    pg.wait_for_selector(".ant-message-notice", timeout=8000)
    return pg.locator(".ant-message-notice").last.inner_text()


def toasts_gone(pg) -> None:
    """等上一条 toast 自己消失再做下一步。

    不等的话下一步会读到**上一条**（antd 的 toast 默认挂 3s，而提交到新 toast
    出现只要几十毫秒）—— 表现是「明明成功了却报失败」，而且时快时慢，
    看着像产品在抽风。这是自测脚本自己的坑，不是被测页面的。
    """
    for _ in range(60):
        if pg.locator(".ant-message-notice").count() == 0:
            return
        pg.wait_for_timeout(200)


def main() -> int:
    cleanup()
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1600, "height": 1100})
        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[type="text"]', "admin")
        pg.fill('input[type="password"]', ADMIN_PW)
        pg.click('button[type="submit"]')
        pg.wait_for_url("**/projects", timeout=20000)

        pg.goto(f"{BASE}/settings/cc-feedback", wait_until="domcontentloaded")
        pg.wait_for_timeout(2500)

        # ── 1. 表里到底有没有行 ──────────────────────────────────
        # 这一条就是空壳 bug 的哨兵：接口少裹一层 data，这里立刻变 0。
        print("== 列表 ==")
        rows = pg.locator(".ant-table-tbody tr.ant-table-row")
        n = rows.count()
        ck(n > 0, f"待处理列出 {n} 行",
           "表是空的 —— 多半是接口没裹 {\"data\": ...}，或者数据没导进去")
        empty = pg.locator("text=CC 还没报过问题")
        ck(empty.count() == 0, "没有落到空态",
           "页面显示空态文案，但库里是有数据的")

        total_txt = pg.locator(".ant-pagination-total-text").inner_text() if \
            pg.locator(".ant-pagination-total-text").count() else ""
        print(f"   分页统计：{total_txt!r}")
        ck("共 0 条" not in total_txt, "分页统计非零", "分页统计是 0")

        # 状态下拉的括号里是 summary 的数（另一条数据通路，空壳时会变成 0）
        # antd v6 的 Select 选中项是 .ant-select-content-has-value
        # （v5 那个 .ant-select-selection-item 已经没有了）
        sel = pg.locator(".ant-select-content-has-value").first.inner_text()
        print(f"   状态筛选：{sel!r}")
        ck("（0）" not in sel, "summary 有数", "summary 全 0 —— summary 那一路没接上")

        shot1 = str(pathlib.Path(OUT) / "cc_feedback_list.png")
        pg.screenshot(path=shot1, full_page=False)
        print(f"   截图 {shot1}")

        # ── 2. 详情抽屉：正文渲染出来没有 ────────────────────────
        print("== 详情抽屉 ==")
        rows.first.locator("a").first.click()
        pg.wait_for_selector(".ant-drawer-body", timeout=8000)
        pg.wait_for_timeout(1200)
        body = pg.locator(".ant-drawer-body pre").first
        blen = len(body.inner_text().strip()) if body.count() else 0
        ck(blen > 40, f"正文渲染出来了（{blen} 字）", "抽屉里没有正文")
        ck(pg.locator(".ant-drawer-body button:has-text('不需要处理')").count() == 1,
           "处置按钮在", "处置按钮没渲染")
        # 还没定类的那些（导入进来的 31 条全是），抽屉里必须能看到上报方报的类 ——
        # 第一个动作就叫「认下并分类」，看不见就不知道自己在认什么。
        head = pg.locator(".ant-drawer-body").inner_text()[:400]
        ck("还没定类" not in head or "上报时报的是" in head,
           "没定类时露出了上报方报的类别",
           "只写「还没定类」，上报方报的那一类没露出来")
        shot2 = str(pathlib.Path(OUT) / "cc_feedback_detail.png")
        pg.screenshot(path=shot2)
        print(f"   截图 {shot2}")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(600)

        # ── 3. 闸门的拒绝理由有没有到人眼前 ──────────────────────
        # 正文太短是**前端不拦、后端拦**的那一种，所以它能真的打到接口。
        # 拒绝里必须带出「该怎么写」——只回一句「提交失败」的话，
        # 录的人只能靠猜，而这段话本身就是这条通道的设计的一部分。
        print("== 拒绝理由（正文太短） ==")
        pg.click("button:has-text('手工录入')")
        pg.wait_for_selector(".ant-modal-container", timeout=6000)
        pg.fill("#title", f"{MARK}正文太短应当被拒 {STAMP}")
        pg.click("#category")
        pg.wait_for_timeout(400)
        pg.locator(".ant-select-dropdown:visible .ant-select-item-option").filter(
            has_text="优化").first.click()
        pg.fill("#body", "太短了")
        pg.locator(".ant-modal-footer button.ant-btn-primary").click()
        t = toast_text(pg)
        print(f"   toast：{t!r}")
        ck("字" in t or "太短" in t, "说清了被拒的原因", f"没说清原因：{t!r}")
        ck(len(t) > 30 and ("期望" in t or "怎么" in t or "三段" in t),
           "把「该怎么写」也带出来了（err.detail 这条链是通的）",
           f"只有结论没有做法 —— err.detail 断在某一节：{t!r}")
        cnt_toast = pg.locator(".ant-message-notice").count()
        ck(cnt_toast == 1, "只弹了一条 toast",
           f"叠了 {cnt_toast} 条 toast（request() 和页面各弹了一次）")

        # ── 4. 写够了就能进，并且真的出现在列表里 ────────────────
        print("== 正常录入 ==")
        toasts_gone(pg)
        pg.fill("#body",
                "自测：在 CC 反馈页手工录入一条，验证闸门放行之后这条能落库、"
                "能出现在待处理里、并且能被处置。这段话只是为了凑够正文下限，"
                "跑完会被脚本自己删掉。")
        pg.locator(".ant-modal-footer button.ant-btn-primary").click()
        t = toast_text(pg)
        print(f"   toast：{t!r}")
        ck("已记录" in t or "并到" in t, "录入成功", f"录入失败：{t!r}")
        pg.wait_for_timeout(1800)
        mine = pg.locator(f".ant-table-tbody tr.ant-table-row:has-text('{MARK}')")
        ck(mine.count() == 1, "新录的这条在列表里", "录进去了但列表里找不到")

        # ── 5. 处置 → 回音 → 它离开待处理 ────────────────────────
        print("== 处置（不需要处理 + 回音） ==")
        mine.first.locator("a").first.click()
        pg.wait_for_selector(".ant-drawer-body", timeout=8000)
        pg.wait_for_timeout(1000)
        toasts_gone(pg)
        pg.locator(".ant-drawer-body button:has-text('不需要处理')").click()
        pg.wait_for_selector(".ant-modal-container", timeout=6000)
        # 先空着提交：这一步该被**前端表单**拦下，压根不该发请求
        pg.locator(".ant-modal-footer button.ant-btn-primary").click()
        pg.wait_for_timeout(600)
        ck(pg.locator(".ant-form-item-explain-error").count() >= 1,
           "回音必填在前端就拦住了", "回音空着也让提交了")
        pg.fill("#resolution", "自测行，处置掉。（这条会被脚本删除）")
        pg.locator(".ant-modal-footer button.ant-btn-primary").click()
        t = toast_text(pg)
        print(f"   toast：{t!r}")
        ck("不需要处理" in t, "处置成功", f"处置失败：{t!r}")
        pg.wait_for_timeout(1200)
        ck(pg.locator(".ant-drawer-body .ant-alert:has-text('不需要处理')").count() == 1,
           "抽屉里给出了「会短路后续上报」的提醒", "短路提醒没出现")
        shot3 = str(pathlib.Path(OUT) / "cc_feedback_triaged.png")
        pg.screenshot(path=shot3)
        print(f"   截图 {shot3}")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(1500)
        ck(pg.locator(f".ant-table-tbody tr.ant-table-row:has-text('{MARK}')").count() == 0,
           "处置完就不在待处理里了", "处置完还挂在待处理列表上")

        b.close()

    cleanup()
    print()
    if fails:
        print(f"✗ {len(fails)} 处没过：")
        for f in fails:
            print("   -", f)
        return 1
    print("✓ 全过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
