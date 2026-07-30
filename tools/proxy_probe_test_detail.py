import asyncio, json, os, socket, subprocess, urllib.request
from playwright.async_api import async_playwright
FRONT="http://127.0.0.1:5173"; BACK="http://127.0.0.1:8756"; IP="192.168.51.108"
SHOTS="/tmp/shots"; os.makedirs(SHOTS, exist_ok=True); fails=[]
def tok():
    r=urllib.request.Request(BACK+"/api/auth/login",method="POST",
        data=json.dumps({"username":"admin","password":"admin123"}).encode(),
        headers={"Content-Type":"application/json"})
    d=json.loads(urllib.request.urlopen(r,timeout=10).read()); return (d.get("data") or d)["token"]
async def main():
    t=tok()
    async with async_playwright() as p:
        b=await p.chromium.launch(); c=await b.new_context(viewport={"width":1440,"height":1000})
        pg=await c.new_page(); errs=[]
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.on("console", lambda m: errs.append(m.text) if m.type=="error" else None)
        await pg.goto(FRONT+"/login", wait_until="domcontentloaded")
        await pg.evaluate("t => localStorage.setItem('token', t)", t)
        await pg.goto(FRONT+"/tools/proxy-probe", wait_until="networkidle")
        await pg.wait_for_timeout(1200)
        urllib.request.urlopen(urllib.request.Request(BACK+"/api/proxy-probe/reset",method="POST",data=b""),timeout=10)
        await pg.wait_for_timeout(1200)
        # 造一条带凭证的转发请求
        subprocess.run(["curl","-s","-o","/dev/null","-x",f"http://svc:secret123@{IP}:28900",
                        f"http://{IP}:28100/v1/models"],timeout=20)
        await pg.wait_for_timeout(2200)
        print("列表行数:", await pg.locator("tbody tr").count())
        # 点行打开抽屉
        await pg.locator("tbody tr").first.click()
        await pg.wait_for_selector("text=① 原始请求", state="visible", timeout=8000)
        await pg.wait_for_timeout(1600)
        body = await pg.locator(".ant-drawer").inner_text()
        checks = {
            "① 原始请求段":       "① 原始请求" in body,
            "② 转发请求段":       "② 转发给上游的请求" in body,
            "③ 上游响应段":       "③ 上游响应" in body,
            "原始是 absolute-URI": "GET http://" in body,
            "转发是 origin-form":  "GET /v1/models HTTP/1.1" in body,
            "列出剥掉的逐跳头":     "Proxy-Authorization" in body and "Proxy-Connection" in body,
            "响应头可见":          "HTTP/1.1 200 OK" in body,
            "响应体预览可见":       '"object":"list"' in body,
            "凭证解码段":          "凭证解码" in body,
            "密码原样显示":        "secret123" in body,
        }
        for k,v in checks.items():
            print(f"  {k}: {v}")
            if not v: fails.append(k)
        shown = "secret123" in body
        print("  抽屉里显示密码 secret123:", shown, "（应为 True —— 原样显示，不做删改）")
        if not shown: fails.append("密码没有原样显示")
        await pg.screenshot(path=f"{SHOTS}/12_detail_drawer.png", full_page=True)
        real=[e for e in errs if "favicon" not in e.lower() and "overlayInnerStyle" not in e]
        print("控制台错误:", real[:3] if real else "无")
        if real: fails.append("控制台报错")
        await b.close()
    print("\n未通过项:", fails if fails else "无，明细抽屉全部通过")
    return 1 if fails else 0
raise SystemExit(asyncio.run(main()))
