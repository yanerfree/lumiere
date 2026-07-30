#!/usr/bin/env python3
"""
验证两条 MUST：
  1. 不得缓冲完整响应体 —— SSE 流式输出必须边到边转发
  2. 请求体要能透传 —— Content-Length 和 Transfer-Encoding: chunked 都要行
上游用裸 TCP 自己实现，好精确控制 chunked / 流式行为；跑完随进程消失。
"""
import socket, threading, time, sys

HOST_IP = "192.168.51.108"        # 改成本机内网 IP
PROXY = (HOST_IP, 28900)           # 代理地址
PORT = 28778                      # 本进程内起的测试上游端口
UP = "%s:%d" % (HOST_IP, PORT)


def read_headers(f):
    head = b""
    while b"\r\n\r\n" not in head:
        c = f.recv(1)
        if not c:
            return None
        head += c
    return head


def upstream():
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(16)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=serve, args=(conn,), daemon=True).start()


def serve(conn):
    try:
        head = read_headers(conn)
        if not head:
            return
        lines = head.split(b"\r\n")
        req = lines[0].decode()
        hdrs = {}
        for l in lines[1:]:
            if b":" in l:
                k, _, v = l.partition(b":")
                hdrs[k.decode().lower()] = v.decode().strip()
        path = req.split()[1]

        if path.startswith("/sse"):
            conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                         b"Cache-Control: no-cache\r\nConnection: close\r\n\r\n")
            for i in range(5):
                conn.sendall(b"data: event-%d\n\n" % i)
                time.sleep(0.4)
            conn.close()
            return

        # /echo：把收到的请求体大小和传输方式报回去（验证请求体透传）
        te = hdrs.get("transfer-encoding", "")
        body = b""
        if "chunked" in te.lower():
            buf = b""
            while True:
                while b"\r\n" not in buf:
                    c = conn.recv(4096)
                    if not c:
                        break
                    buf += c
                if b"\r\n" not in buf:
                    break
                line, _, buf = buf.partition(b"\r\n")
                size = int(line.split(b";")[0], 16)
                if size == 0:
                    break
                while len(buf) < size + 2:
                    c = conn.recv(4096)
                    if not c:
                        break
                    buf += c
                body += buf[:size]
                buf = buf[size + 2:]
            mode = "chunked"
        else:
            n = int(hdrs.get("content-length", 0))
            while len(body) < n:
                c = conn.recv(4096)
                if not c:
                    break
                body += c
            mode = "content-length"
        # 顺带回报：上游看到的请求行必须是 origin-form，且没有 Proxy-* 头
        proxy_hdrs = [k for k in hdrs if k.startswith("proxy-")]
        payload = ('{"mode":"%s","received":%d,"request_line":"%s","proxy_headers":%d}'
                   % (mode, len(body), req, len(proxy_hdrs))).encode()
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                     % (len(payload), payload))
        conn.close()
    except Exception as exc:
        print("上游异常:", exc)


threading.Thread(target=upstream, daemon=True).start()
time.sleep(0.3)
fails = []

# ---- 1. SSE 流式：首字节必须早于整体结束 ----
print("========== 不缓冲响应体（SSE 流式）==========")
s = socket.create_connection(PROXY, 5)
s.settimeout(15)
s.sendall(("GET http://%s/sse HTTP/1.1\r\nHost: %s\r\nAccept: text/event-stream\r\n\r\n"
           % (UP, UP)).encode())
t0 = time.monotonic()
first_event_at = None
arrivals = []
data = b""
while True:
    c = s.recv(4096)
    if not c:
        break
    data += c
    if b"data: event-" in c:
        arrivals.append(round(time.monotonic() - t0, 2))
        if first_event_at is None:
            first_event_at = time.monotonic() - t0
total = time.monotonic() - t0
s.close()
got = data.count(b"data: event-")
print("收到事件数: %d（期望 5）" % got)
print("各事件到达时刻(秒): %s" % arrivals)
print("首个事件 %.2fs / 全部结束 %.2fs" % (first_event_at or -1, total))
if got == 5 and first_event_at is not None and first_event_at < 0.35 and total > 1.5:
    print("判定: 通过 —— 事件是逐个流过来的，不是攒齐了一次性给（若缓冲则首字节≈总耗时）")
else:
    print("判定: 失败")
    fails.append("SSE 流式")

# ---- 2. 请求体：Content-Length ----
print("\n========== 请求体透传：Content-Length ==========")
body = b"x" * 5000
s = socket.create_connection(PROXY, 5)
s.settimeout(10)
s.sendall(("POST http://%s/echo HTTP/1.1\r\nHost: %s\r\nContent-Length: %d\r\n\r\n"
           % (UP, UP, len(body))).encode() + body)
resp = b""
while True:
    c = s.recv(4096)
    if not c:
        break
    resp += c
s.close()
print(resp.split(b"\r\n\r\n", 1)[-1].decode())
ok = b'"received":5000' in resp and b'"proxy_headers":0' in resp
print("判定: %s（上游收到 5000 字节 + 无 Proxy-* 残留）" % ("通过" if ok else "失败"))
if not ok:
    fails.append("Content-Length 请求体")

# ---- 3. 请求体：Transfer-Encoding chunked ----
print("\n========== 请求体透传：Transfer-Encoding chunked ==========")
s = socket.create_connection(PROXY, 5)
s.settimeout(10)
s.sendall(("POST http://%s/echo HTTP/1.1\r\nHost: %s\r\n"
           "Transfer-Encoding: chunked\r\nProxy-Authorization: Basic c3ZjOnNlY3JldDEyMw==\r\n\r\n"
           % (UP, UP)).encode())
time.sleep(0.1)
for part in (b"a" * 1000, b"b" * 2000, b"c" * 300):   # 分多帧、间隔发，模拟真实流式
    s.sendall(b"%x\r\n%s\r\n" % (len(part), part))
    time.sleep(0.05)
s.sendall(b"0\r\n\r\n")
resp = b""
while True:
    c = s.recv(4096)
    if not c:
        break
    resp += c
s.close()
print(resp.split(b"\r\n\r\n", 1)[-1].decode())
ok = b'"mode":"chunked"' in resp and b'"received":3300' in resp and b'"proxy_headers":0' in resp
print("判定: %s（chunked 透传 3300 字节 + Proxy-Authorization 已剥除）" % ("通过" if ok else "失败"))
if not ok:
    fails.append("chunked 请求体")

# ---- 4. 请求行必须是 origin-form ----
print("\n========== 请求行改写为 origin-form ==========")
import re
m = re.search(rb'"request_line":"([^"]+)"', resp)
print("上游实际看到的请求行: %s" % (m.group(1).decode() if m else "?"))
ok = bool(m) and m.group(1).startswith(b"POST /echo ") and b"http://" not in m.group(1)
print("判定: %s（不含 absolute-URI）" % ("通过" if ok else "失败"))
if not ok:
    fails.append("origin-form 改写")

print("\n未通过项: %s" % (fails if fails else "无，全部通过"))
sys.exit(1 if fails else 0)
