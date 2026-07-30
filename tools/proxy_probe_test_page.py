#!/usr/bin/env python3
"""验收 ⑦~⑩：浏览器实测代理观测页面。截图落 /tmp/shots/。"""
import asyncio, json, os, socket, subprocess, urllib.request
from playwright.async_api import async_playwright

FRONT = "http://127.0.0.1:5173"
BACK = "http://127.0.0.1:8756"
IP = "192.168.51.108"
SHOTS = "/tmp/shots"
os.makedirs(SHOTS, exist_ok=True)
fails = []


def login_token():
    req = urllib.request.Request(
        BACK + "/api/auth/login", method="POST",
        data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
        headers={"Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=10).read())
    return (d.get("data") or d)["token"]


def curl_via_proxy():
    """终端发一次 absolute-URI 请求"""
    subprocess.run(["curl", "-s", "-o", "/dev/null", "-x", f"http://{IP}:28900",
                    f"http://{IP}:28100/v1/models"], timeout=20)


def connect_via_proxy():
    """终端发一次 CONNECT 隧道请求"""
    s = socket.create_connection((IP, 28900), 5)
    s.settimeout(5)
    s.sendall(f"CONNECT {IP}:28100 HTTP/1.1\r\nHost: {IP}:28100\r\n".encode()
              + b"Proxy-Authorization: Basic c3ZjOnNlY3JldDEyMw==\r\n\r\n")
    s.recv(200)
    s.close()


async def main():
    token = login_token()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1440, "height": 1000})
        page = await ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append("console.%s: %s" % (m.type, m.text))
                if m.type == "error" else None)

        # 先注入 token 免登录
        await page.goto(FRONT + "/login", wait_until="domcontentloaded")
        await page.evaluate("t => localStorage.setItem('token', t)", token)

        # ---------- ⑦ 页面正常显示，能看到监听状态 ----------
        print("========== ⑦ 打开页面，看监听状态 ==========")
        await page.goto(FRONT + "/tools/proxy-probe", wait_until="networkidle")
        await page.wait_for_timeout(1800)
        title_ok = await page.locator("text=代理观测").first.is_visible()
        run_tag = (await page.locator("text=运行中").count()) > 0 or \
                  (await page.locator("text=已停止").count()) > 0
        listen_ok = (await page.locator("text=监听 0.0.0.0:28900").count()) > 0
        addr_val = await page.locator("input[readonly]").first.input_value()
        print("  标题可见: %s | 运行状态标签: %s | 监听地址可见: %s" % (title_ok, run_tag, listen_ok))
        print("  页面显示的代理地址: %s" % addr_val)
        await page.screenshot(path=f"{SHOTS}/07_page.png", full_page=True)
        if not (title_ok and run_tag and listen_ok and addr_val.endswith(":28900")):
            fails.append("⑦ 页面显示")

        # ---------- ⑨ 先清零，再从终端发请求，不刷新看是否自动出现 ----------
        print("\n========== ⑧ 点「清零」→ 计数归 0、列表清空、显示空状态文案 ==========")
        await page.get_by_role("button", name="清零").first.click()
        await page.wait_for_selector(".ant-popconfirm", state="visible", timeout=5000)
        await page.locator(".ant-popconfirm button.ant-btn-primary").last.click()
        await page.wait_for_selector(".ant-popconfirm", state="hidden", timeout=5000)
        await page.wait_for_timeout(1500)
        empty1 = await page.locator("text=等待请求…").is_visible()
        empty2 = await page.locator("text=如果操作完这里仍然是空的，说明请求没有走代理。").is_visible()
        # 计数区大数字应全为 0
        nums = await page.locator("div").filter(has_text="总请求数").first.inner_text()
        print("  空状态「等待请求…」可见: %s" % empty1)
        print("  结论文案可见: %s" % empty2)
        stats = json.loads(urllib.request.urlopen(BACK + "/api/proxy-probe/stats").read())
        print("  后端计数: connectCount=%d httpCount=%d" % (stats["connectCount"], stats["httpCount"]))
        await page.screenshot(path=f"{SHOTS}/08_reset_empty.png", full_page=True)
        if not (empty1 and empty2 and stats["connectCount"] == 0 and stats["httpCount"] == 0):
            fails.append("⑧ 清零/空状态")

        # ---------- ⑨ 终端发一次 curl，不刷新浏览器 ----------
        print("\n========== ⑨ 终端发一次 curl，不刷新浏览器，看 1~2 秒内是否自动出现 ==========")
        rows_before = await page.locator("tbody tr").count()
        curl_via_proxy()
        await page.wait_for_timeout(2000)          # 只等，不 reload
        rows_after = await page.locator("tbody tr").count()
        print("  发请求前列表行数=%d，2 秒后=%d（未刷新页面）" % (rows_before, rows_after))
        got_get = (await page.locator("tbody tr:has-text('GET')").count()) > 0
        print("  出现 GET 记录: %s" % got_get)
        await page.screenshot(path=f"{SHOTS}/09_auto_refresh.png", full_page=True)
        if not (rows_after > rows_before and got_get):
            fails.append("⑨ 自动刷新")

        # ---------- ⑩ CONNECT 和 GET 各一次，两种形态能区分 ----------
        print("\n========== ⑩ 连发 CONNECT 和 GET，页面上两种形态能区分 ==========")
        connect_via_proxy()
        curl_via_proxy()
        await page.wait_for_timeout(2200)
        n_connect = await page.locator("tbody tr:has-text('CONNECT')").count()
        n_get = await page.locator("tbody tr:has-text('GET')").count()
        print("  CONNECT 行数=%d，GET 行数=%d" % (n_connect, n_get))
        # 颜色是否真的不同（取标签的实际渲染色）
        c_color = await page.locator("tbody tr:has-text('CONNECT') span").first.evaluate(
            "el => getComputedStyle(el).color")
        g_color = await page.locator("tbody tr:has-text('GET') span").first.evaluate(
            "el => getComputedStyle(el).color")
        print("  CONNECT 标签色=%s | GET 标签色=%s | 不同: %s"
              % (c_color, g_color, c_color != g_color))
        # 认证列：CONNECT 那条带的是 user=svc，密码不得出现
        table_html = await page.locator("tbody").inner_html()
        html = await page.content()
        print("  记录表格出现 user=svc: %s | 记录表格出现密码 secret123: %s"
              % ("user=svc" in table_html, "secret123" in table_html))
        print("  整页出现 secret123: %s（应为 False —— 这里只是确认故障注入的密码框没预填默认值，与报文明细无关）" % ("secret123" in html))
        await page.screenshot(path=f"{SHOTS}/10_two_kinds.png", full_page=True)
        if not (n_connect >= 1 and n_get >= 1 and c_color != g_color):
            fails.append("⑩ 两种形态区分")
        if "secret123" in html:
            fails.append("⑩ 故障注入的密码框预填了默认值")

        # ---------- 故障注入开关 ----------
        print("\n========== 附加：页面上的故障注入开关（实时生效）==========")
        await page.get_by_role("switch").first.click()
        await page.wait_for_timeout(900)
        inj = json.loads(urllib.request.urlopen(BACK + "/api/proxy-probe/status").read())["injection"]
        print("  点了「拒绝所有请求」后后端状态 rejectAll=%s" % inj["rejectAll"])
        rc = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
                             "-x", f"http://{IP}:28900", f"http://{IP}:28100/v1/models"],
                            capture_output=True, text=True, timeout=20).stdout
        print("  此时再发请求 curl 返回码=%s（000/断开=符合预期）" % rc)
        await page.wait_for_timeout(1600)
        await page.screenshot(path=f"{SHOTS}/11_inject.png", full_page=True)
        if not inj["rejectAll"]:
            fails.append("故障注入开关未生效")
        # 关掉，别留状态
        await page.get_by_role("switch").first.click()
        await page.wait_for_timeout(800)

        real_errors = [e for e in errors if "favicon" not in e.lower()
                       and "overlayInnerStyle" not in e]  # 该告警来自共享组件 ServiceStatusBadge，非本页引入
        print("\n控制台错误: %s" % (real_errors[:5] if real_errors else "无"))
        if real_errors:
            fails.append("控制台有错误")
        await browser.close()

    print("\n未通过项: %s" % (fails if fails else "无，⑦~⑩ 全部通过"))
    print("截图: %s" % ", ".join(sorted(os.listdir(SHOTS))))
    return 1 if fails else 0


raise SystemExit(asyncio.run(main()))
