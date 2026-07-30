#!/usr/bin/env python3
"""
回归：列表不许出现重复行。

原 bug：轮询按 since 做增量追加，而轮询有三个触发源可能并发，
并发时同一批记录会被 concat 两遍 —— 一次连接在页面上变成 2~3 行。
这里用「发 N 次请求 -> 页面必须恰好 N 行」来卡，并额外狂点刷新制造并发轮询。
"""
import asyncio, json, socket, subprocess, urllib.request
from playwright.async_api import async_playwright

FRONT = "http://127.0.0.1:5173"
BACK = "http://127.0.0.1:8756"
IP = "192.168.51.108"
API = BACK + "/api/proxy-probe"
fails = []


def token():
    req = urllib.request.Request(
        BACK + "/api/auth/login", method="POST",
        data=json.dumps({"username": "admin", "password": "admin123"}).encode(),
        headers={"Content-Type": "application/json"})
    d = json.loads(urllib.request.urlopen(req, timeout=10).read())
    return (d.get("data") or d)["token"]


def api_post(path):
    urllib.request.urlopen(urllib.request.Request(API + path, method="POST", data=b""), timeout=10)


def backend_count():
    d = json.loads(urllib.request.urlopen(API + "/records?limit=200", timeout=10).read())
    return len(d["records"]), d["stats"]


def one_get():
    subprocess.run(["curl", "-s", "-o", "/dev/null", "-x", f"http://{IP}:28900",
                    f"http://{IP}:28100/v1/models"], timeout=20)


def one_connect():
    s = socket.create_connection((IP, 28900), 5)
    s.settimeout(5)
    s.sendall(f"CONNECT {IP}:28100 HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    s.recv(200)
    s.close()


async def rows(page):
    return await page.locator("tbody tr").count()


async def main():
    t = token()
    async with async_playwright() as p:
        b = await p.chromium.launch()
        c = await b.new_context(viewport={"width": 1440, "height": 1000})
        page = await c.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append(m.text)
                if m.type == "error" and "overlayInnerStyle" not in m.text else None)
        await page.goto(FRONT + "/login", wait_until="domcontentloaded")
        await page.evaluate("t => localStorage.setItem('token', t)", t)
        await page.goto(FRONT + "/tools/proxy-probe", wait_until="networkidle")
        await page.wait_for_timeout(1500)

        # ---- 1. 清零后逐条发，每次都核对「页面行数 == 后端记录数」----
        api_post("/reset")
        await page.wait_for_timeout(1600)
        print("清零后页面行数:", await rows(page))
        if await rows(page) != 0:
            fails.append("清零后列表没清空")

        for i in range(1, 4):
            one_get()
            await page.wait_for_timeout(1800)
            n_page, n_back = await rows(page), backend_count()[0]
            print(f"  第 {i} 次 GET -> 页面 {n_page} 行 / 后端 {n_back} 条  "
                  f"{'OK' if n_page == n_back == i else 'FAIL'}")
            if not (n_page == n_back == i):
                fails.append(f"第 {i} 次 GET 行数不符（页面 {n_page} 后端 {n_back} 期望 {i}）")

        one_connect()
        await page.wait_for_timeout(1800)
        n_page, n_back = await rows(page), backend_count()[0]
        print(f"  再 1 次 CONNECT -> 页面 {n_page} 行 / 后端 {n_back} 条  "
              f"{'OK' if n_page == n_back == 4 else 'FAIL'}")
        if not (n_page == n_back == 4):
            fails.append(f"CONNECT 后行数不符（页面 {n_page} 后端 {n_back} 期望 4）")

        # ---- 2. 让轮询必然重叠：拦截接口加 1.5s 延迟（轮询间隔 1s）----
        # 本机响应只要几毫秒，不加延迟根本叠不上，测不出原 bug。
        print("\n给 /records 加 1.5s 延迟（间隔 1s，必然重叠），再发 3 次请求：")

        async def slow(route):
            if delay_on["v"]:
                await asyncio.sleep(1.5)
            try:
                await route.continue_()
            except Exception:
                pass          # 页面已关/已放行，忽略

        delay_on = {"v": True}
        await page.route("**/proxy-probe/records*", slow)
        api_post("/reset")
        await page.wait_for_timeout(4000)
        for _ in range(3):
            one_get()
            await page.wait_for_timeout(400)
        # 同一时刻再狂点刷新，叠更多并发轮询
        for _ in range(6):
            await page.get_by_role("button", name="刷新").click()
        await page.wait_for_timeout(9000)
        n_page, n_back = await rows(page), backend_count()[0]
        print(f"  页面 {n_page} 行 / 后端 {n_back} 条  {'OK' if n_page == n_back == 3 else 'FAIL'}")
        if not (n_page == n_back == 3):
            fails.append(f"并发轮询下行数不符（页面 {n_page} 后端 {n_back} 期望 3）")

        # 页面行数必须等于后端计数 —— 这正是截图里「总请求数 4 但列表 6 行」暴露的问题
        _, st = backend_count()
        total = st["connectCount"] + st["httpCount"]
        print(f"  页面行数 {n_page} vs 后端计数 {total}  "
              f"{'OK' if n_page == total else 'FAIL（列表多出来的就是重复行）'}")
        if n_page != total:
            fails.append(f"页面行数({n_page}) != 后端计数({total})")
        delay_on["v"] = False        # 关掉延迟（不 unroute，避免和挂起的处理器打架）
        await page.wait_for_timeout(2500)

        # ---- 3. 行的唯一键必须是后端 id（不能用时间戳/展示文本 ——
        #         同一秒打同一个地址的多条请求，显示出来的文字是一样的）----
        ids = await page.evaluate(
            "() => [...document.querySelectorAll('tbody tr')].map(tr => tr.dataset.recId)")
        print("  页面行数:", len(ids), "| 不同的记录 id 数:", len(set(ids)),
              "| id 列表:", ids)
        if len(ids) != len(set(ids)):
            fails.append("页面存在重复的记录 id（同一条记录渲染了多次）")
        if any(i is None for i in ids):
            fails.append("行上没有 data-rec-id，无法按 id 判重")

        # ---- 4. 清零后新请求仍能出现（原 since 高水位会把新 id 过滤掉）----
        print("\n清零后再发请求，验证新记录仍会出现（原实现会被 since 高水位吃掉）：")
        api_post("/reset")
        await page.wait_for_timeout(1600)
        one_get()
        await page.wait_for_timeout(2000)
        n_page, n_back = await rows(page), backend_count()[0]
        print(f"  页面 {n_page} 行 / 后端 {n_back} 条  {'OK' if n_page == n_back == 1 else 'FAIL'}")
        if not (n_page == n_back == 1):
            fails.append(f"清零后新记录未出现（页面 {n_page} 后端 {n_back} 期望 1）")

        # ---- 5. 顺序：最新在最上面 ----
        one_connect()
        await page.wait_for_timeout(1800)
        first_row = await page.locator("tbody tr").first.inner_text()
        print("\n最上面一行是否为刚发的 CONNECT:", "CONNECT" in first_row)
        if "CONNECT" not in first_row:
            fails.append("最新记录没排在最上面")

        print("\n控制台错误:", errs[:3] if errs else "无")
        if errs:
            fails.append("控制台报错")
        await b.close()

    print("\n未通过项:", fails if fails else "无，列表刷新逻辑全部通过")
    return 1 if fails else 0


raise SystemExit(asyncio.run(main()))
