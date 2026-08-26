#!/usr/bin/env python3
"""对 Lumiere 后端那一份代理监听器跑：并发 / SSE 不缓冲 / chunked 请求体 / origin-form。"""
import json, re, socket, subprocess, threading, time, urllib.request

HOST_IP = "192.168.51.108"
PROXY = (HOST_IP, 28900)
API = "http://127.0.0.1:8756/api/proxy-probe"
SLOW_PORT, SSE_PORT = 28781, 28782
N = 20
fails = []


def api_post(path):
    req = urllib.request.Request(API + path, method="POST", data=b"")
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


def api_get(path):
    return json.loads(urllib.request.urlopen(API + path, timeout=10).read())


def read_head(conn):
    head = b""
    while b"\r\n\r\n" not in head:
        c = conn.recv(1)
        if not c:
            return None
        head += c
    return head


def serve(port, handler):
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", port))
    srv.listen(32)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=handler, args=(conn,), daemon=True).start()


def slow_handler(conn):
    try:
        if read_head(conn) is None:
            return
        time.sleep(2)                       # 模拟大模型慢响应
        b = b'{"slow":true}\n'
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                     % (len(b), b))
    finally:
        conn.close()


def sse_handler(conn):
    """SSE：每 0.4s 一个事件，验证不缓冲。同时 /echo 验请求体透传。"""
    try:
        head = read_head(conn)
        if head is None:
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
                         b"Connection: close\r\n\r\n")
            for i in range(5):
                conn.sendall(b"data: event-%d\n\n" % i)
                time.sleep(0.4)
            return
        body = b""
        if "chunked" in hdrs.get("transfer-encoding", "").lower():
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
        proxy_hdrs = [k for k in hdrs if k.startswith("proxy-")]
        p = ('{"mode":"%s","received":%d,"request_line":"%s","proxy_headers":%d}'
             % (mode, len(body), req, len(proxy_hdrs))).encode()
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
                     % (len(p), p))
    finally:
        conn.close()


threading.Thread(target=serve, args=(SLOW_PORT, slow_handler), daemon=True).start()
threading.Thread(target=serve, args=(SSE_PORT, sse_handler), daemon=True).start()
time.sleep(0.4)

# ---------- 并发 ----------
print("========== 并发正确性（上游每条 2s）==========")
api_post("/reset")
results = {}


def one(i):
    t0 = time.monotonic()
    try:
        s = socket.create_connection(PROXY, 10)
        s.settimeout(30)
        s.sendall(("GET http://%s:%d/v1/models HTTP/1.1\r\nHost: h\r\n\r\n"
                   % (HOST_IP, SLOW_PORT)).encode())
        data = b""
        while b'{"slow":true}' not in data:
            c = s.recv(65536)
            if not c:
                break
            data += c
        s.close()
        results[i] = (b'{"slow":true}' in data, time.monotonic() - t0)
    except Exception as e:
        results[i] = (False, str(e))


ths = [threading.Thread(target=one, args=(i,)) for i in range(N)]
t0 = time.monotonic()
for t in ths:
    t.start()
for t in ths:
    t.join(40)
el = time.monotonic() - t0
ok = sum(1 for v in results.values() if v[0])
print("  成功 %d/%d，总耗时 %.2fs（并发应≈2s，串行会是 %ds）" % (ok, N, el, 2 * N))
print("  判定: %s" % ("通过" if ok == N and el < 8 else "失败"))
if not (ok == N and el < 8):
    fails.append("并发")
st = api_get("/stats")
print("  /stats httpCount=%d errors=%d -> %s"
      % (st["httpCount"], st["errors"], "PASS" if st["httpCount"] == N and st["errors"] == 0 else "FAIL"))
if st["httpCount"] != N or st["errors"] != 0:
    fails.append("并发计数")

# ---------- SSE 不缓冲 ----------
print("\n========== 不缓冲响应体（SSE 流式）==========")
s = socket.create_connection(PROXY, 5)
s.settimeout(15)
s.sendall(("GET http://%s:%d/sse HTTP/1.1\r\nHost: h\r\n\r\n" % (HOST_IP, SSE_PORT)).encode())
t0 = time.monotonic()
arr, data = [], b""
while True:
    c = s.recv(4096)
    if not c:
        break
    data += c
    if b"data: event-" in c:
        arr.append(round(time.monotonic() - t0, 2))
s.close()
got = data.count(b"data: event-")
print("  事件数 %d，到达时刻 %s，总耗时 %.2fs" % (got, arr, time.monotonic() - t0))
sse_ok = got == 5 and arr and arr[0] < 0.35 and arr[-1] > 1.2
print("  判定: %s（逐个流过来，不是攒齐一次性给）" % ("通过" if sse_ok else "失败"))
if not sse_ok:
    fails.append("SSE 不缓冲")

# ---------- 请求体 ----------
print("\n========== 请求体透传 + origin-form + Proxy-* 剥除 ==========")
body = b"x" * 5000
s = socket.create_connection(PROXY, 5)
s.settimeout(10)
s.sendall(("POST http://%s:%d/echo HTTP/1.1\r\nHost: h\r\nContent-Length: %d\r\n\r\n"
           % (HOST_IP, SSE_PORT, len(body))).encode() + body)
r1 = b""
while True:
    c = s.recv(4096)
    if not c:
        break
    r1 += c
s.close()
print("  Content-Length: %s" % r1.split(b"\r\n\r\n", 1)[-1].decode().strip())
if b'"received":5000' not in r1 or b'"proxy_headers":0' not in r1:
    fails.append("Content-Length 请求体")

s = socket.create_connection(PROXY, 5)
s.settimeout(10)
s.sendall(("POST http://%s:%d/echo HTTP/1.1\r\nHost: h\r\nTransfer-Encoding: chunked\r\n"
           "Proxy-Authorization: Basic c3ZjOnNlY3JldDEyMw==\r\n\r\n"
           % (HOST_IP, SSE_PORT)).encode())
for part in (b"a" * 1000, b"b" * 2000, b"c" * 300):
    s.sendall(b"%x\r\n%s\r\n" % (len(part), part))
    time.sleep(0.05)
s.sendall(b"0\r\n\r\n")
r2 = b""
while True:
    c = s.recv(4096)
    if not c:
        break
    r2 += c
s.close()
print("  chunked: %s" % r2.split(b"\r\n\r\n", 1)[-1].decode().strip())
if b'"mode":"chunked"' not in r2 or b'"received":3300' not in r2 or b'"proxy_headers":0' not in r2:
    fails.append("chunked 请求体")
m = re.search(rb'"request_line":"([^"]+)"', r2)
print("  上游看到的请求行: %s" % (m.group(1).decode() if m else "?"))
if not (m and m.group(1).startswith(b"POST /echo ") and b"http://" not in m.group(1)):
    fails.append("origin-form 改写")

print("\n未通过项: %s" % (fails if fails else "无，全部通过"))
raise SystemExit(1 if fails else 0)
