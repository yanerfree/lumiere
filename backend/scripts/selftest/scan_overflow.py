"""全站横向溢出扫描 —— 找"把父容器撑破"的元素。

为什么不用 grep：这类 bug 的根因是 CSS 计算结果（flex 子项 min-width:auto
不肯缩），源码里长得跟正常代码一模一样。只有真渲染出来量一遍才找得全。

判据：元素右边缘越过父容器内容区右边缘 > 4px，且父容器 overflow-x 不是
auto/scroll（那种是有意让它滚的，不算 bug），且元素不是绝对/固定定位
（浮层、下拉本来就允许越界）。
"""
import json, sys
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"
PROJ = "35804052-f08e-4775-be0a-66ba0775618e"
BR = "738a5170-4138-4b5b-abd8-4dc80532c483"
CASE = "6e16ab76-d6b8-4d73-a662-389da1563af8"
OUT = "/tmp/claude-1000/-home-dreamer-testBench/702e0056-ce16-4f34-b04c-0ae355a0cba3/scratchpad"

ROUTES = [
    ("项目列表", "/projects"),
    ("用例管理", f"/projects/{PROJ}/cases"),
    ("用例详情", f"/projects/{PROJ}/cases/{CASE}?branchId={BR}"),
    ("API 接口", f"/projects/{PROJ}/apis"),
    ("测试计划", f"/projects/{PROJ}/plans"),
    ("测试报告", f"/projects/{PROJ}/reports"),
    ("探索测试", f"/projects/{PROJ}/exploratory"),
    ("文档管理", f"/projects/{PROJ}/documents"),
    ("自动化数据", f"/projects/{PROJ}/settings/automation-data"),
    ("国际化词典", f"/projects/{PROJ}/settings/i18n"),
    ("AI 能力总览", f"/projects/{PROJ}/settings/ai-capabilities"),
    ("Skill 管理", f"/projects/{PROJ}/settings/skills"),
    ("MCP 工具", f"/projects/{PROJ}/settings/mcp-tools"),
    ("项目 AI 配置", f"/projects/{PROJ}/settings/ai"),
    ("环境配置", "/settings/env"),
    ("AI 服务配置", "/settings/ai-providers"),
    ("通知渠道", "/settings/channels"),
    ("用户管理", "/settings/users"),
    ("操作日志", "/settings/logs"),
    ("服务与端口", "/settings/services"),
    ("协议 Mock", "/tools/api-mock"),
    ("LLM Mock", "/tools/llm-mock"),
    ("MCP Mock", "/tools/mcp-mock"),
    ("OAuth2 Mock", "/tools/oauth2-mock"),
    ("代理观测", "/tools/proxy-probe"),
    ("HTTP 请求", "/tools/http-client"),
    ("工具箱", "/tools/toolbox"),
    ("压力测试", "/tools/load-test"),
]

DETECT = """() => {
  const out = [];
  const seen = new Set();
  for (const el of document.querySelectorAll('body *')) {
    const p = el.parentElement;
    if (!p || p === document.body) continue;
    const cs = getComputedStyle(el);
    if (cs.position === 'absolute' || cs.position === 'fixed') continue;
    if (cs.display === 'none' || cs.visibility === 'hidden') continue;
    const ps = getComputedStyle(p);
    // 父容器自己会滚 / 会裁剪的，不算"撑破"
    if (['auto','scroll','hidden'].includes(ps.overflowX)) continue;
    const r = el.getBoundingClientRect(), pr = p.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) continue;
    // antd <Row gutter> 用负 margin 往外顶 gutter/2，是设计不是 bug；
    // 列内部的 padding 会把它补回来，视觉上不越界。实测正好等于 6px/8px。
    const ml = parseFloat(cs.marginLeft) || 0, mr = parseFloat(cs.marginRight) || 0;
    if (ml < 0 || mr < 0) continue;
    const padR = parseFloat(ps.paddingRight) || 0;
    const over = r.right - (pr.right - padR);
    if (over <= 4) continue;
    // 只报最外层那个，别把它内部的子元素也一条条报出来
    const path = el.tagName.toLowerCase() + '.' + (el.className || '').toString().split(' ').slice(0,2).join('.');
    const key = path + '|' + Math.round(over);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push({ el: path, over: Math.round(over),
               text: (el.innerText || '').slice(0, 48).replace(/\\n/g, ' ') });
  }
  return out.sort((a,b) => b.over - a.over).slice(0, 6);
}"""

with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1600, "height": 1000})
    pg.goto(f"{BASE}/login", wait_until="networkidle")
    pg.fill('input[type="text"]', "admin")
    pg.fill('input[type="password"]', "admin123")
    pg.click('button[type="submit"]')
    pg.wait_for_url("**/projects", timeout=15000)

    # 两个视口都扫。这类 bug 的本质是"内容比容器宽"，窄屏下才暴露的很常见 ——
    # 只在大屏扫会漏掉一半。1280 是最常见的笔记本工作区宽度。
    bad = {}
    for vw in (1600, 1280):
      pg.set_viewport_size({"width": vw, "height": 1000})
      print(f"\n── 视口 {vw}px ──")
      for name, route in ROUTES:
        try:
            # 有的页面在轮询，networkidle 永远等不到 —— 用 domcontentloaded + 固定等待
            pg.goto(BASE + route, wait_until="domcontentloaded", timeout=25000)
            pg.wait_for_timeout(2200)
            hits = pg.evaluate(DETECT)
        except Exception as e:
            print(f"  ⚠ {name} 打不开：{str(e)[:60]}")
            continue
        if hits:
            bad[f"{vw}/{name}"] = hits
            print(f"  ❌ {name}")
            for h in hits:
                print(f"       超出 {h['over']}px  <{h['el']}>  {h['text']!r}")
        else:
            print(f"  ✅ {name}")
    b.close()

print()
print(f"扫了 {len(ROUTES)} 个页面 × 2 个视口，{len(bad)} 处横向溢出")
sys.exit(1 if bad else 0)
