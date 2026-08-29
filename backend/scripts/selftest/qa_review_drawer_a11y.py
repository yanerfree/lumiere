"""QA 对账 · 评审抽屉：结论词 + **每一句话的对比度**，都量出来。

为什么要有这个脚本 —— 2026-08-29 用户就两句话：「部分认领不算数，这是什么意思」
和「页面上的灰色字有点看不清」。查下来两句是同一个 bug 的两面：
抽屉里那些解释「这个数怎么来的」的小字用的是 `C.faint`，白底 **1.6:1**，
基本等于没显示。人看到的就只剩几个光数字 —— 而"看不懂"和"没显示"在截图里长得一样。

**颜色不许靠眼睛验，也不许靠公式算。**
第一版 hint 按 WCAG 公式算出 4.56:1 就定了，真跑起来量到 4.48:1 ——
差在页面背景不是纯白（全站有淡渐变），而公式默认它是。0.08 的差，正好压在线下。
所以这里是在**真渲染出来的页面上**逐个文字节点量，背景沿 DOM 往上找第一个不透明的。

跑法（要先起后端 8756 和前端）：

    LUMIERE_WEB=http://127.0.0.1:5173 LUMIERE_PROJ=<项目UUID> \
      backend/.venv/bin/python backend/scripts/selftest/qa_review_drawer_a11y.py

项目得有至少一条 `done` 的域评审（页面上那一列点得开的徽标）。
**退出码有意义**：0 = 全过，1 = 有低于线的 / 徽标没找到。
（同目录那个 `qa_dropped_no_anchor.py` 没找到徽标时打一行就 exit 0 ——
  自测工具自己坏了不出声，等于把自测悄悄取消了。这里不重蹈。）
"""
import os
import pathlib
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from app.services.qa_catalog_review import AXES, VERDICT_CN  # noqa: E402

BASE = os.environ.get("LUMIERE_WEB", "http://127.0.0.1:5173")
PROJ = os.environ.get("LUMIERE_PROJ", "1a1fb724-e252-4fd2-a7f1-3bc6bfdc5cbe")
OUT = os.environ.get("LUMIERE_OUT", "/tmp")

AA = 4.5          # WCAG AA 正文线。这一页的说明字 11–13px，比"正文"还小，只会更难读。
MIN_LEN = 6       # 只量承载句子的：更短的是数字、优先级标签、`—` 占位

# ── 具名例外 ──
# 例外只许**指名道姓**，不许调阈值。调阈值是把所有未来的回归一起放过去，
# 而例外只放过这一条，且写清为什么、谁能改。
ALLOW = {
    "给人看": "选中态页签，用的是 global.css 的全站主色 --primary(#0ea5a0)。"
              "那个色同时在渐变、按钮底色里用，改它是全站设计决定，不是这一页能定的。",
}


def allowed(text: str) -> str | None:
    for k, why in ALLOW.items():
        if k in text:
            return why
    return None


CONTRAST_JS = r"""
() => {
  const lum = (r,g,b) => {
    const f = c => { c/=255; return c<=0.03928 ? c/12.92 : Math.pow((c+0.055)/1.055,2.4) }
    return 0.2126*f(r)+0.7152*f(g)+0.0722*f(b)
  }
  const parse = s => (s.match(/\d+(\.\d+)?/g)||[]).map(Number)
  // 背景沿 DOM 往上找第一个**不透明**的祖先 —— 元素自己多半是 transparent，
  // 直接读 el 的 backgroundColor 会拿到 rgba(0,0,0,0)，把对比度算成天文数字。
  const bgOf = el => {
    for (let n=el; n; n=n.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor)
      if (c.length>=3 && (c.length<4 || c[3]>0.5)) return c
    }
    return [255,255,255]
  }
  const drawer = document.querySelector('.ant-drawer-body')
  if (!drawer) return null
  const out = []
  const walk = n => {
    if (n.nodeType===3) {
      const t = n.textContent.trim()
      if (t.length>=MIN_LEN && n.parentElement) {
        const el = n.parentElement
        if (!el.offsetParent && getComputedStyle(el).position!=='fixed') return
        const st = getComputedStyle(el)
        const fg = parse(st.color), bg = bgOf(el)
        const a = fg.length>3 ? fg[3] : 1
        const mix = [0,1,2].map(i => fg[i]*a + bg[i]*(1-a))   // 前景带透明度时按合成后算
        const L1 = lum(...mix), L2 = lum(...bg.slice(0,3))
        out.push({t: t.slice(0,46), ratio: +(((Math.max(L1,L2)+0.05)/(Math.min(L1,L2)+0.05)).toFixed(2)),
                  color: st.color, size: parseFloat(st.fontSize)})
      }
      return
    }
    for (const c of n.childNodes) walk(c)
  }
  walk(drawer)
  return out
}
""".replace("MIN_LEN", str(MIN_LEN))


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch()
        pg = b.new_page(viewport={"width": 1500, "height": 1100})
        pg.goto(f"{BASE}/login", wait_until="networkidle")
        pg.fill('input[type="text"]', "admin")
        pg.fill('input[type="password"]', "admin123")
        pg.click('button[type="submit"]')
        pg.wait_for_url("**/projects", timeout=20000)
        pg.goto(f"{BASE}/projects/{PROJ}/qa-catalog", wait_until="domcontentloaded")
        pg.wait_for_timeout(4500)

        # 徽标文案从 `VERDICT_CN` 现生成，**不许抄一份进来** —— 抄的那份失效时不报错。
        want = list(VERDICT_CN.values())
        tag = pg.locator("span.ant-tag:text-matches('%s')" % "|".join(want)).first
        print("== 结论徽标 ==")
        if not tag.count():
            allt = pg.locator("span.ant-tag")
            print(f"   ✗ 三种文案一个都没匹配上：{want}")
            print("     页面上的 tag：",
                  [allt.nth(i).inner_text() for i in range(min(allt.count(), 8))])
            print("     （多半是前端 VERDICT 和后端 VERDICT_CN 漂了，两边都得改）")
            b.close()
            return 1
        print(f"   ✓ {tag.inner_text()!r}")

        tag.click()
        pg.wait_for_timeout(2500)
        shot = str(pathlib.Path(OUT) / "qa_review_drawer.png")
        pg.screenshot(path=shot)

        rows = pg.evaluate(CONTRAST_JS)
        if rows is None:
            print("   ✗ 抽屉没打开")
            b.close()
            return 1

        # ── 分好组的维度表到底画出来没有 ──
        # 这条比对比度更要紧：`dims` 只在**详情**接口里发，列表接口故意不发。
        # 抽屉曾经一直拿列表行直接渲染，于是这张表**在正常路径上从来没画出来过**，
        # 人看到的是一列 `skip` `assert` 这样的裸键 —— 而那个降级分支自己不报错、
        # 页面也不空白，看上去只是"这版就长这样"。**没有这一条，它可以再坏一次没人知道。**
        # 轴名从后端 `AXES` 现取，不抄。
        body = pg.locator(".ant-drawer-body").inner_text()
        axes = [a[1] for a in AXES]
        missing = [n for n in axes if n not in body]
        print("\n== 维度表（三个轴：%s）==" % "、".join(axes))
        if missing:
            print(f"   ✗ 没画出来，缺：{missing}")
            print("     抽屉多半又在拿列表行渲染了（列表接口不发 dims），"
                  "或后端跑着没有 dims 字段的旧代码")
            b.close()
            return 1
        if "还没拿到维度口径" in body:
            print("   ✗ 三个轴名在，但降级那段也在 —— 两套同时渲染了")
            b.close()
            return 1
        print("   ✓ 三个轴都在，且没走降级分支")

        bad, waived = [], []
        for r in sorted(rows, key=lambda r: r["ratio"]):
            if r["ratio"] >= AA:
                continue
            (waived if allowed(r["t"]) else bad).append(r)

        print(f"\n== 抽屉里承载句子的文字节点：{len(rows)} 个（AA 线 {AA}:1）==")
        for r in waived:
            print(f"   ~ 例外 {r['ratio']}:1 {r['t']!r}\n     {allowed(r['t'])}")
        if bad:
            print(f"   ✗ {len(bad)} 个低于线：")
            for r in bad:
                print(f"     {r['ratio']:>5}:1 {r['size']:>5}px {r['color']:<22} {r['t']!r}")
        else:
            print("   ✓ 除具名例外外全部达标")
        print(f"\n截图: {shot}")
        b.close()
        return 1 if bad else 0


sys.exit(main())
