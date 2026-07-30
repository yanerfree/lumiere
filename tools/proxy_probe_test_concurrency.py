#!/usr/bin/env python3
"""验收⑤：并发正确性。慢上游(2s/请求)跑在本进程内，测完随进程一起消失。"""
import socket, subprocess, threading, time, json, re, sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HOST_IP = "192.168.51.108"        # 改成本机内网 IP
PROXY = (HOST_IP, 28900)           # 代理地址
LOG = "/tmp/proxy.log"            # 改成启动时用的 --log-file
SLOW_PORT = 28777                 # 本进程内起的慢上游端口
UP = "%s:%d" % (HOST_IP, SLOW_PORT)
N = 20


class SlowHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        time.sleep(2)                      # 模拟大模型慢响应
        body = b'{"slow":true}\n'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def one_request(idx, results):
    t0 = time.monotonic()
    try:
        s = socket.create_connection(PROXY, 10)
        s.settimeout(30)
        req = ("GET http://%s/v1/models HTTP/1.1\r\nHost: %s\r\n"
               "User-Agent: conc-test/%d\r\n\r\n" % (UP, UP, idx)).encode()
        s.sendall(req)
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
            if b'{"slow":true}' in data:   # 拿到完整 body 就算成功
                break
        s.close()
        status = data.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        ok = b'{"slow":true}' in data
        results[idx] = (ok, status, time.monotonic() - t0)
    except Exception as exc:
        results[idx] = (False, "EXC %s: %s" % (type(exc).__name__, exc),
                        time.monotonic() - t0)


srv = ThreadingHTTPServer(("0.0.0.0", SLOW_PORT), SlowHandler)
srv.daemon_threads = True
threading.Thread(target=srv.serve_forever, daemon=True).start()
time.sleep(0.3)

# 基线：直连慢上游确认真的慢
t0 = time.monotonic()
one_baseline = {}
b = socket.create_connection((HOST_IP, SLOW_PORT), 5)
b.sendall(b"GET /v1/models HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
while b.recv(65536):
    pass
b.close()
print("直连慢上游单条耗时: %.2fs（确认上游确实慢）" % (time.monotonic() - t0))

subprocess.run(["curl", "-sS", "-X", "POST", "http://127.0.0.1:28901/reset"],
               stdout=subprocess.DEVNULL, check=True)
with open(LOG, encoding="utf-8") as f:
    before = sum(1 for _ in f)

results = {}
threads = [threading.Thread(target=one_request, args=(i, results)) for i in range(N)]
t0 = time.monotonic()
for t in threads:
    t.start()
for t in threads:
    t.join(40)
elapsed = time.monotonic() - t0

ok_count = sum(1 for v in results.values() if v[0])
print("\n%d 条并发经代理：成功 %d/%d，总耗时 %.2fs"
      % (N, ok_count, N, elapsed))
print("  -> 并发正确应≈2s（单条耗时）；串行会是 %ds" % (2 * N))
print("  -> 判定: %s" % ("通过（远小于串行）" if elapsed < 8 else "失败（疑似串行/阻塞）"))
slowest = max((v[2] for v in results.values()), default=0)
print("  -> 单条最慢 %.2fs" % slowest)
for i in sorted(results):
    if not results[i][0]:
        print("     失败#%d: %s" % (i, results[i][1]))

time.sleep(0.4)
with open(LOG, encoding="utf-8") as f:
    new = f.read().splitlines()[before:]
pat = re.compile(r"^\[\d{2}:\d{2}:\d{2}\] GET http://%s/v1/models \(no-auth\)$"
                 % re.escape(UP))
good = [l for l in new if pat.match(l)]
bad = [l for l in new if not pat.match(l)]
print("\n新增日志行: %d（期望 %d）" % (len(new), N))
print("严格匹配单行格式(=无交错): %d" % len(good))
print("畸形/交错行: %d %s" % (len(bad), bad if bad else ""))

out = subprocess.run(["curl", "-s", "http://127.0.0.1:28901/stats"],
                     capture_output=True, text=True).stdout
st = json.loads(out)
print("\n统计接口: %s" % json.dumps(st, ensure_ascii=False))
print("断言 http_count==%d : %s" % (N, "PASS" if st["http_count"] == N else "FAIL"))
print("断言 errors==0      : %s" % ("PASS" if st["errors"] == 0 else "FAIL"))
srv.shutdown()
sys.exit(0 if (ok_count == N and not bad and st["http_count"] == N) else 1)
