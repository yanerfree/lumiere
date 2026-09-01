"""CC 反馈页 · 活体自测：**从人的角度**把这一页走一遍。

为什么非要有这一份 —— 2026-09-01 这一版接口层漏了 `{"data": ...}` 那层壳。
后端 200、前端 `res.data.items` 拿到 undefined，页面渲染成一张空表：
**看着像「CC 还没报过问题」，而不是「接错了」**。单测全绿，没有一条会红。
所以这里咬的不是接口，是**页面上到底有没有东西**。

2026-09-01 第二版（AI 落处置、人只兜底）之后这份自测跟着换了咬的地方：

  · **「手工录入」没了**，所以原来那三节（正文太短被拒 → 改够 → 处置）全部作废。
    连带丢掉的是「后端拒绝理由有没有到人眼前」那一条 —— 页面上**再没有一条
    前端放行、后端拦下**的路了（triage 的三道校验前端都是 required）。
    那条链现在由 `tests/api/cc_feedback/test_cc_feedback_flow.py` 在接口层咬
    （断言 400 的 `detail` 里带着出路），别在这里重复造一个假的入口来测它。
  · 换上来的四条都是**只有真开浏览器才验得了**的：
      ① 列序就是需求那一串（列是人扫这一页的顺序，错了没人报错）
      ②「等人拍板」筛档 —— 它**跨状态**（AI 说判不了的挂在 new 上、AI 判的
        wont_fix 被新证据撬开的挂在 wont_fix 上）。按 status 筛必漏后一种，
        而漏了的表现是「人以为没事要办」。
      ③ 抽屉横幅分得清 AI 判的 wont_fix（可翻案）和人判的（终局）——
        这两句话就是这条通道敢让 AI 落 wont_fix 的全部前提。
      ④ 批量：勾选数进按钮文案、进度条真的动、跑完横幅报结果。
        没有进度人会以为它卡死然后重复点，而那是**顺序单例**，点了也只是排队。

跑法（先起后端 8756 + 一个前端 vite）：

    LUMIERE_WEB=http://127.0.0.1:5188 \
      backend/.venv/bin/python backend/scripts/selftest/cc_feedback_page.py

**退出码有意义**：0 = 全过，1 = 有一步没过。
自测数据自己收尾：标题带 `自测·` 前缀的行跑完就删（不删的话，第二趟会撞上
「不需要处理」的指纹短路，走出一条和第一趟完全不同的路 —— 而那种不稳定
最难查）。**播种也走 psql**：页面上已经没有录入入口了，而这一页要看的几种形状
（等人拍板、AI 判的 wont_fix、人判的 wont_fix）只能从库里摆出来。

`LUMIERE_SKIP_BATCH=1` 可以跳掉第 ④ 节 —— 那一节**真打模型**，限流时会走降级
通道，几分钟起。跳掉它别的照跑。
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
# 播种和收尾用的库名。默认是开发库；跑在别的库上（比如临时起的一套）时用它改，
# 否则自测行播/删到了**另一个库**上 —— 当场不报错，第二趟才炸。
DB = os.environ.get("LUMIERE_DB", "lumiere")
ADMIN_PW = os.environ.get("LUMIERE_ADMIN_PASSWORD", "admin123")
SKIP_BATCH = os.environ.get("LUMIERE_SKIP_BATCH") == "1"
MARK = "自测·"
STAMP = time.strftime("%H%M%S")

# 列序就是需求给的那一串。表头第一个是勾选框那一格（空的），过滤掉再比。
WANT = ["标题", "优先级", "类别", "状态", "撞了几次", "最近一次", "来源", "操作"]

# 四种形状，各自要验的东西写在注释里。severity 只有 high/medium/low
# （填 P0/P1 那种会静默落到「待定」上，看着像页面没渲染）。
BODY = ("自测播的一条，用来验证 CC 反馈页的列序、筛档和抽屉横幅。"
        "这段话只是为了像一条真反馈那么长，跑完会被脚本自己删掉。")
SEED = [
    # ① 还没判过 + 没定类 → 批量勾它；也用来验「上报方报的类别」露没露出来
    dict(key="batch", title=f"{MARK}还没判过的一条 {STAMP}", status="new",
         reported_category="bug", category=None, severity=None, occurrences=3,
         tool_name="lum_get_api_test", decided_by=None, needs_human=None,
         resolution=None, handled_by=None),
    # ② 等人拍板·形状 A：AI 自己说判不了，**还挂在 new 上**
    dict(key="nh_new", title=f"{MARK}AI 说判不了的一条 {STAMP}", status="new",
         reported_category="bug", category="bug", severity="medium", occurrences=2,
         tool_name="lum_check_assertion_bite", decided_by="ai",
         needs_human="需求没写清：残留该由谁收 —— 工具自己收还是调用方收，得有人定。",
         resolution=None, handled_by=None),
    # ③ 等人拍板·形状 B：AI 判过 wont_fix，被带新证据的重报撬开 → **挂在 wont_fix 上**
    #    按 status 筛「等人拍板」的话，漏的就是这一种
    dict(key="nh_wf", title=f"{MARK}被新证据撬开的一条 {STAMP}", status="wont_fix",
         reported_category="bug", category="bug", severity="medium", occurrences=3,
         tool_name="lum_list_cases", decided_by="ai",
         needs_human="AI 上次判了不需要处理，这次重报带了上次没有的现象，要人看一眼翻不翻案。",
         resolution="不是缺陷：lifecycle_status 不传时已自动排除 deprecated。",
         handled_by=None),
    # ④ 人判的 wont_fix → 抽屉要说这是**终局**
    dict(key="wf_human", title=f"{MARK}人判过不需要处理的一条 {STAMP}", status="wont_fix",
         reported_category="bug", category="bug", severity="high", occurrences=7,
         tool_name="lum_get_api_test", decided_by="human",
         needs_human=None,
         resolution="真因是后端重启踢的会话（event_store=None 不可续传），不是响应体积。"
                    "查法：grep 'Created new transport' 对时间。",
         handled_by="自测"),
]

fails: list[str] = []


def ck(cond: bool, ok: str, bad: str) -> bool:
    print(("   ✓ " + ok) if cond else ("   ✗ " + bad))
    if not cond:
        fails.append(bad)
    return cond


def psql(sql: str) -> str:
    r = subprocess.run(["psql", "-h", "localhost", "-U", "postgres", "-d", DB, "-tAc", sql],
                       env={**os.environ, "PGPASSWORD": "postgres"},
                       capture_output=True, text=True, check=False)
    if r.returncode != 0:
        print("   psql 失败：", (r.stderr or "").strip()[:300])
    return (r.stdout or "").strip()


def q(v) -> str:
    return "null" if v is None else "'" + str(v).replace("'", "''") + "'"


def cleanup() -> None:
    """删掉自测行。**只删自己播的那些**（标题前缀），别的一个不碰 ——
    页面上根本没有「删除」这个动作，这条通道的设计就是不给删（删掉等于把回音和
    复发计数一起抹了），所以这里走 psql，而且范围咬死在自测前缀上。"""
    psql(f"delete from cc_feedback where title like '{MARK}%'")


def seed() -> None:
    cols = ("source, reporter, tool_name, fingerprint, title, body, reported_category, "
            "category, severity, occurrences, status, decided_by, needs_human, "
            "resolution, handled_by, handled_at")
    for i, r in enumerate(SEED):
        vals = ", ".join([
            "'import'", "'自测脚本'", q(r["tool_name"]),
            q(f"selftest{STAMP}{i:02d}"), q(r["title"]), q(BODY),
            q(r["reported_category"]), q(r["category"]), q(r["severity"]),
            str(r["occurrences"]), q(r["status"]), q(r["decided_by"]),
            q(r["needs_human"]), q(r["resolution"]), q(r["handled_by"]),
            "now()" if r["handled_by"] else "null",
        ])
        psql(f"insert into cc_feedback ({cols}) values ({vals})")
    n = psql(f"select count(*) from cc_feedback where title like '{MARK}%'")
    print(f"   播了 {n} 条自测行（跑完删）")


def title_of(key: str) -> str:
    return next(r["title"] for r in SEED if r["key"] == key)


def status_select(pg):
    """状态那个下拉。默认值是「待处理（N）」，清空之后才显示 placeholder「状态」——
    所以不能按 placeholder 找，得按当前文案认。"""
    sels = pg.locator(".ant-select")
    for i in range(sels.count()):
        t = (sels.nth(i).inner_text() or "").strip()
        if "待处理" in t or "等人拍板" in t or t == "状态":
            return sels.nth(i)
    return None


def clear_status(pg) -> None:
    """清掉状态筛档 = 看全量。**这一步不能省**：页面默认只列待处理，
    wont_fix 那两条默认根本不在表里，忘了清就会得出「数据没播进去」的错结论。"""
    s = status_select(pg)
    if s is None:
        return
    s.hover()
    pg.wait_for_timeout(300)
    clr = s.locator(".ant-select-clear")
    if clr.count():
        clr.first.click(force=True)
    pg.wait_for_timeout(1800)


def open_row(pg, title: str):
    """点标题打开抽屉，返回抽屉正文文本。

    ⚠ 点的必须是标题那个 `<a>`，不能点整格：这一列 `ellipsis`，短标题只占格子左边
    一小截，点格子中间落在空白上 —— **什么都不会发生，也不报错**，表现成
    「抽屉打不开」这种像产品坏了的假红。
    """
    row = pg.locator(".ant-table-tbody tr.ant-table-row", has_text=title)
    if not row.count():
        return None
    row.first.locator("a").first.click()
    pg.wait_for_selector(".ant-drawer-body", timeout=8000)
    pg.wait_for_timeout(1200)
    return pg.locator(".ant-drawer-body").inner_text()


def main() -> int:
    cleanup()
    print("== 播种 ==")
    seed()
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1680, "height": 1100})
        errs: list[str] = []
        pg.on("console", lambda m: errs.append(m.text) if m.type == "error" else None)
        bad_req: list[str] = []
        pg.on("response", lambda r: bad_req.append(f"{r.status} {r.request.method} {r.url}")
              if r.status >= 400 and "/api/" in r.url else None)
        # 静态资源的失败**按 URL 判**，不按控制台那句话判：控制台只会说
        # 「Failed to load resource: 403」，一个字都不提是谁 —— 拿它当判据只能整类放过
        # 或者整类当红，两种都不对。
        res_bad: list[str] = []
        pg.on("response", lambda r: res_bad.append(f"{r.status} {r.request.resource_type} {r.url}")
              if r.status >= 400 and "/api/" not in r.url else None)

        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[type="text"]', "admin")
        pg.fill('input[type="password"]', ADMIN_PW)
        pg.click('button[type="submit"]')
        pg.wait_for_url("**/projects", timeout=20000)

        pg.goto(f"{BASE}/settings/cc-feedback", wait_until="domcontentloaded")
        pg.wait_for_timeout(2800)
        rows = pg.locator(".ant-table-tbody tr.ant-table-row")

        # ── 1. 表里到底有没有行 + 列序 ───────────────────────────
        # 空表这一条就是空壳 bug 的哨兵：接口少裹一层 data，行数立刻变 0。
        print("== 列表 ==")
        n = rows.count()
        ck(n > 0, f"待处理列出 {n} 行",
           "表是空的 —— 多半是接口没裹 {\"data\": ...}，或者数据没播进去")
        ck(pg.locator("text=CC 还没报过问题").count() == 0, "没有落到空态",
           "页面显示空态文案，但库里是有数据的")

        heads = [h.strip() for h in pg.locator("thead th").all_inner_texts() if h.strip()]
        print("   表头：", heads)
        ck(heads == WANT, f"列序就是需求那一串：{'/'.join(WANT)}",
           f"列序不对：拿到 {heads}，要的是 {WANT}")
        # 人不会去手工录反馈（都是 CC 报进来的），这个入口 2026-09-01 撤了
        ck("手工录入" not in pg.content(), "没有「手工录入」入口",
           "页面上还有「手工录入」—— 这个入口已经撤了")

        total_txt = pg.locator(".ant-pagination-total-text").inner_text() if \
            pg.locator(".ant-pagination-total-text").count() else ""
        print(f"   分页统计：{total_txt!r}")
        ck("共 0 条" not in total_txt, "分页统计非零", "分页统计是 0")

        # 状态下拉的括号里是 summary 的数（另一条数据通路，空壳时会变成 0）
        sel = pg.locator(".ant-select-content-has-value").first.inner_text()
        print(f"   状态筛选：{sel!r}")
        ck("（0）" not in sel, "summary 有数", "summary 全 0 —— summary 那一路没接上")

        shot1 = str(pathlib.Path(OUT) / "cc_feedback_list.png")
        pg.screenshot(path=shot1, full_page=False)
        print(f"   截图 {shot1}")

        # ── 2. 详情抽屉：正文 + 未定类时露出上报方报的类 ──────────
        print("== 详情抽屉 ==")
        head = open_row(pg, title_of("batch")) or ""
        ck(len(head) > 40, "抽屉打开了", "抽屉没打开或者是空的")
        body = pg.locator(".ant-drawer-body pre").first
        blen = len(body.inner_text().strip()) if body.count() else 0
        ck(blen > 40, f"正文渲染出来了（{blen} 字）", "抽屉里没有正文")
        ck(pg.locator(".ant-drawer-body button:has-text('不需要处理')").count() == 1,
           "人拍板的按钮还在（AI 落处置之后人依然能改判）", "处置按钮没渲染")
        # 还没定类的那些，抽屉里必须能看到上报方报的类 —— 第一个动作就叫
        # 「认下并分类」，看不见就不知道自己在认什么。
        ck("还没定类" not in head[:600] or "上报时报的是" in head,
           "没定类时露出了上报方报的类别",
           "只写「还没定类」，上报方报的那一类没露出来")
        shot2 = str(pathlib.Path(OUT) / "cc_feedback_detail.png")
        pg.screenshot(path=shot2)
        print(f"   截图 {shot2}")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(700)

        # ── 3. 「等人拍板」筛档：跨状态 ───────────────────────────
        print("== 等人拍板（跨状态） ==")
        s = status_select(pg)
        ck(s is not None, "找到状态下拉", "找不到状态下拉")
        if s is not None:
            s.click()
            pg.wait_for_timeout(700)
            opt = pg.locator(".ant-select-item-option", has_text="等人拍板")
            ck(opt.count() > 0,
               f"下拉里有这一档：{opt.first.inner_text().strip() if opt.count() else ''}",
               "状态下拉里找不到「等人拍板」")
            if opt.count():
                opt.first.click()
                pg.wait_for_timeout(2200)
                txt = pg.locator(".ant-table-tbody").inner_text()
                # 一条挂 new、一条挂 wont_fix —— 按 status 筛会漏掉后者
                ck(title_of("nh_new") in txt and title_of("nh_wf") in txt,
                   "跨状态都在：挂 new 的那条 + 挂 wont_fix 的那条",
                   "「等人拍板」漏了跨状态的那一种（挂在 wont_fix 上的没筛出来）")
                shot3 = str(pathlib.Path(OUT) / "cc_feedback_awaiting_human.png")
                pg.screenshot(path=shot3, full_page=False)
                print(f"   截图 {shot3}")

        # ── 4. 抽屉横幅：AI 判的能翻案 / 人判的是终局 ─────────────
        print("== 抽屉横幅（AI 判的 vs 人判的） ==")
        pg.reload(wait_until="domcontentloaded")
        pg.wait_for_timeout(2500)
        clear_status(pg)

        t_ai = open_row(pg, title_of("nh_wf")) or ""
        ck("重报" in t_ai or "不会永久关死" in t_ai,
           "AI 判的「不需要处理」：抽屉说了带新证据重报能翻案",
           f"AI 判的 wont_fix 抽屉里没有翻案那句：{t_ai[:140]}")
        shot4 = str(pathlib.Path(OUT) / "cc_feedback_wontfix_ai.png")
        pg.screenshot(path=shot4)
        print(f"   截图 {shot4}")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(700)

        t_h = open_row(pg, title_of("wf_human")) or ""
        ck("短路" in t_h or "终局" in t_h,
           "人判的「不需要处理」：抽屉说了这是终局（会短路后续上报）",
           f"人判的 wont_fix 抽屉看不出终局：{t_h[:140]}")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(700)

        t_nh = open_row(pg, title_of("nh_new")) or ""
        ck("判不了" in t_nh or "等人拍板" in t_nh,
           "等人拍板：抽屉写了 AI 缺的是什么（不是一个光秃秃的 true）",
           f"needs_human 抽屉没写缺什么：{t_nh[:140]}")
        pg.keyboard.press("Escape")
        pg.wait_for_timeout(700)

        # ── 5. 批量处理：勾选 → 进度 → 跑完 ──────────────────────
        # 真打模型，所以只勾**自己播的那一条**。不勾就是「全部待处理」——
        # 那会把开发库里真的积压一次推完，不是自测该干的事。
        if SKIP_BATCH:
            print("== 批量处理 ==（LUMIERE_SKIP_BATCH=1，跳过）")
        else:
            print("== 批量处理 ==")
            box = pg.locator(f".ant-table-tbody tr.ant-table-row:has-text('{title_of('batch')}')"
                             " .ant-checkbox-input")
            ck(box.count() == 1, "找到自测那一行的勾选框", "找不到自测行的勾选框")
            if box.count():
                box.first.click()
                pg.wait_for_timeout(500)
                btn = pg.locator("button", has_text="批量处理").first
                label = btn.inner_text().strip()
                print("   按钮文案：", label)
                ck("1" in label, f"按钮跟着勾选数变：{label}", f"按钮没跟着勾选变：{label}")
                btn.click()
                pg.wait_for_timeout(2500)
                alert = pg.locator(".ant-alert")
                seen = ""
                for _ in range(80):   # 顺序跑，限流时一条要几十秒
                    txts = [alert.nth(i).inner_text() for i in range(alert.count())]
                    cur = " | ".join(t.replace("\n", " ") for t in txts
                                     if "批量" in t or "上一批" in t)
                    if cur and cur != seen:
                        print("   横幅：", cur[:160])
                        seen = cur
                    if "上一批处理完了" in cur:
                        break
                    pg.wait_for_timeout(1500)
                ck("上一批处理完了" in seen, "批量跑完了，横幅报了结果",
                   f"批量没等到结果（限流时会很慢，可以 LUMIERE_SKIP_BATCH=1 跳过）：{seen[:140]}")
                ck(pg.locator(".ant-progress").count() > 0,
                   "进度条渲染出来了（没有它人会以为卡死然后重复点）", "没看到进度条")
                shot5 = str(pathlib.Path(OUT) / "cc_feedback_batch.png")
                pg.screenshot(path=shot5, full_page=False)
                print(f"   截图 {shot5}")

        # ── 6. 控制台 / 接口 ─────────────────────────────────────
        print("== 控制台 ==")
        depr = [e for e in errs if e.startswith("Warning: [antd:") and "deprecated" in e]
        ctx = [e for e in errs if "Static function can not consume context" in e]
        # 这一句在控制台里不带 URL，判据交给下面的 res_bad（那边有 URL）
        res = [e for e in errs if "Failed to load resource" in e]
        noisy = [e for e in errs if e not in depr and e not in ctx and e not in res]
        print(f"   {len(errs)} 条 = 存量弃用 {len(depr)} + message 静态 {len(ctx)}"
              f" + 资源 {len(res)}（按 URL 判，见下）+ 其它 {len(noisy)}")
        for e in noisy[:5]:
            print("   [其它]", e[:160])
        ck(not noisy, "没有新的控制台报错", f"控制台报错：{noisy[:3]}")
        ck(not bad_req, "没有 4xx/5xx 接口", f"接口报错：{bad_req[:4]}")

        # vite 的 `@fs` 白名单只放行自己那棵树。worktree 里 node_modules 是指回主
        # checkout 的符号链接，字体于是解析成主 checkout 的绝对路径 → 403。
        # **那是跑法造成的，不是页面的毛病**（在主 checkout 上起 vite 就没有），
        # 所以单独摘出来；除它以外的资源失败照旧算红。
        wt_font = [x for x in res_bad if "/@fs/" in x and "node_modules" in x]
        other_res = [x for x in res_bad if x not in wt_font]
        if wt_font:
            print(f"   （摘掉 {len(wt_font)} 条 worktree 字体 403：vite @fs 白名单，跑法问题）")
        for x in other_res[:5]:
            print("   [资源]", x[:160])
        ck(not other_res, "静态资源没有别的失败", f"资源加载失败：{other_res[:3]}")

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
