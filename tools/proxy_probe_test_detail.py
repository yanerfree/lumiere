#!/usr/bin/env python3
"""
明细抽屉验收：两跳必须分得清「别人给代理的」和「代理转发出去的」，
CONNECT 的隧道内容必须明确说清到底加密没加密。

覆盖三条记录：
  1. absolute-URI 转发   -> 两跳都有 HTTP 请求/响应，能看出 origin-form 改写和剥头
  2. CONNECT 打明文端口   -> 隧道内是明文 HTTP，应当直接看到内容（类型 http）
  3. CONNECT 打 TLS 端口  -> 隧道内是 TLS，应当明确说「确认加密」并给出 0x16 依据
"""
import asyncio, json, os, socket, subprocess, urllib.request
from playwright.async_api import async_playwright

FRONT = "http://127.0.0.1:5173"
BACK = "http://127.0.0.1:8756"
API = BACK + "/api/proxy-probe"
IP = "192.168.51.108"
TLS_PORT = 8443          # 本机一个 HTTPS 服务，用来产生真 TLS 隧道
SHOTS = "/tmp/shots"
os.makedirs(SHOTS, exist_ok=True)
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


def make_forward():
    subprocess.run(["curl", "-s", "-o", "/dev/null", "-x",
                    f"http://svc:secret123@{IP}:28900", f"http://{IP}:28100/v1/models"],
                   timeout=20)


def make_plain_tunnel():
    s = socket.create_connection((IP, 28900), 5)
    s.settimeout(6)
    s.sendall(f"CONNECT {IP}:28100 HTTP/1.1\r\nHost: x\r\n\r\n".encode())
    s.recv(100)
    s.sendall(b"GET /v1/models HTTP/1.1\r\nHost: h\r\nConnection: close\r\n\r\n")
    try:
        while s.recv(65536):
            pass
    except Exception:
        pass
    s.close()


def make_tls_tunnel():
    env = dict(os.environ, NODE_USE_ENV_PROXY="1", http_proxy=f"http://{IP}:28900",
               NODE_TLS_REJECT_UNAUTHORIZED="0")
    subprocess.run(["node", "-e",
                    f"fetch('https://{IP}:{TLS_PORT}/').then(()=>0).catch(()=>0)"],
                   env=env, capture_output=True, timeout=25)


async def open_row(page, row_index):
    await page.locator("tbody tr").nth(row_index).click()
    await page.wait_for_selector(".ant-drawer .ant-tabs", state="visible", timeout=8000)
    await page.wait_for_timeout(1200)


async def tab(page, name):
    await page.locator(".ant-drawer .ant-tabs-tab", has_text=name).click()
    await page.wait_for_timeout(700)
    return await page.locator(".ant-drawer .ant-tabs-tabpane-active").inner_text()


async def close_drawer(page):
    # 用 Esc 关，不依赖关闭按钮的 aria-label（antd 版本/语言包会变）
    await page.keyboard.press("Escape")
    await page.wait_for_selector(".ant-drawer .ant-tabs", state="hidden", timeout=8000)
    await page.wait_for_timeout(500)


def check(label, cond):
    print(f"    {label}: {cond}")
    if not cond:
        fails.append(label)


async def main():
    t = token()
    api_post("/start")
    api_post("/reset")
    make_forward()
    make_plain_tunnel()
    make_tls_tunnel()
    await asyncio.sleep(1)

    async with async_playwright() as p:
        b = await p.chromium.launch()
        c = await b.new_context(viewport={"width": 1500, "height": 1050})
        page = await c.new_page()
        errs = []
        page.on("pageerror", lambda e: errs.append(str(e)))
        page.on("console", lambda m: errs.append(m.text)
                if m.type == "error" and "overlayInnerStyle" not in m.text else None)
        await page.goto(FRONT + "/login", wait_until="domcontentloaded")
        await page.evaluate("t => localStorage.setItem('token', t)", t)
        await page.goto(FRONT + "/tools/proxy-probe", wait_until="networkidle")
        await page.wait_for_timeout(1800)

        rows = await page.locator("tbody tr").count()
        kinds = await page.evaluate(
            "() => [...document.querySelectorAll('tbody tr')].map("
            "tr => tr.children[1].innerText.trim() + '|' + tr.children[2].innerText.trim())")
        print("列表行数:", rows, kinds)

        # ---------- 1) absolute-URI 转发 ----------
        idx = next(i for i, k in enumerate(kinds) if k.startswith("GET"))
        print("\n【1】absolute-URI 转发（第 %d 行）" % (idx + 1))
        await open_row(page, idx)
        t1 = await tab(page, "客户端 ⇆ 代理")
        check("① 有「客户端发给代理的请求」", "客户端发给代理的请求" in t1)
        check("① 请求行是 absolute-URI", "GET http://" in t1)
        check("① 能看到 Proxy-Authorization 原值", "Basic c3ZjOnNlY3JldDEyMw==" in t1)
        check("① 有「代理回给客户端的应答」", "代理回给客户端的应答" in t1)
        check("① 应答是 200 OK", "HTTP/1.1 200 OK" in t1)
        t2 = await tab(page, "代理 ⇆ 上游")
        check("② 有「代理发给上游的请求」", "代理发给上游的请求" in t2)
        check("② 请求行改写成 origin-form", "GET /v1/models HTTP/1.1" in t2)
        check("② 转发报文里没有 Proxy-Authorization 原值",
              "Basic c3ZjOnNlY3JldDEyMw==" not in t2)
        check("② 列出剥掉的逐跳头", "Proxy-Authorization" in t2 and "Proxy-Connection" in t2)
        check("② 有「上游回给代理的响应」", "上游回给代理的响应" in t2)
        check("② 响应体可见", '"object":"list"' in t2)
        await close_drawer(page)

        # ---------- 2) CONNECT 打明文端口 ----------
        idx = next(i for i, k in enumerate(kinds) if k.startswith("CONNECT") and ":28100" in k)
        print("\n【2】CONNECT 打明文 28100（第 %d 行）" % (idx + 1))
        await open_row(page, idx)
        t1 = await tab(page, "客户端 ⇆ 代理")
        check("① 请求是 CONNECT", "CONNECT 192.168.51.108:28100" in t1)
        check("① 应答是 200 Connection Established", "200 Connection Established" in t1)
        check("① 明确写出这行是代理自己生成的", "代理自己生成的" in t1)
        t2 = await tab(page, "代理 ⇆ 上游")
        check("② 说明 CONNECT 不发 HTTP 请求", "无 HTTP 请求" in t2)
        check("② 上游响应处说明这一跳没有 HTTP 响应", "没有 HTTP 响应" in t2)
        t3 = await tab(page, "隧道内数据")
        check("③ 判定为明文 HTTP", "明文 HTTP" in t3)
        check("③ 能直接看到隧道里的请求", "GET /v1/models HTTP/1.1" in t3)
        check("③ 不应误报成加密", "确认是 TLS 加密流量" not in t3)
        await close_drawer(page)

        # ---------- 3) CONNECT 打 TLS 端口 ----------
        tls_rows = [i for i, k in enumerate(kinds)
                    if k.startswith("CONNECT") and f":{TLS_PORT}" in k]
        if not tls_rows:
            print("\n【3】没有产生 TLS 隧道记录（本机 %d 端口可能没有 HTTPS 服务）" % TLS_PORT)
            fails.append("未能产生 TLS 隧道记录，加密判定没验到")
        else:
            print("\n【3】CONNECT 打 TLS %d（第 %d 行）" % (TLS_PORT, tls_rows[0] + 1))
            await open_row(page, tls_rows[0])
            label = await page.locator(".ant-drawer .ant-tabs-tab").last.inner_text()
            check("③ 标签页标题直接标出 TLS 加密", "TLS 加密" in label)
            t3 = await tab(page, "隧道内数据")
            check("③ 明确「确认是 TLS 加密流量」", "确认是 TLS 加密流量" in t3)
            check("③ 给出判定依据 0x16", "0x16" in t3)
            check("③ 给出十六进制原文", "16 03 01" in t3)
            check("③ 说明不解密", "不解密" in t3)
            await page.screenshot(path=f"{SHOTS}/13_tunnel_tls.png", full_page=True)
            await close_drawer(page)

        print("\n控制台错误:", errs[:3] if errs else "无")
        if errs:
            fails.append("控制台报错")
        await b.close()

    print("\n未通过项:", fails if fails else "无，明细两跳 + 加密判定全部通过")
    return 1 if fails else 0


raise SystemExit(asyncio.run(main()))
