#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
proxy_probe —— HTTP 正向代理观测仪器（测试工具，非生产组件）

用途：验证被测系统「配了出站代理，请求是否真的走了代理」。
      有日志 = 走了代理；没日志 = 没走代理。

只依赖 Python 3 标准库（3.8+），单文件，直接丢到测试机上跑：

    python3 proxy_probe.py --log-file /tmp/proxy.log

支持两种代理协议形态：
  · CONNECT 隧道        —— Node.js / undici 走这条（注意：明文 http:// 上游也会发 CONNECT）
  · absolute-URI 转发   —— Go net/http Transport 走这条

不做的事：TLS 中间人、SOCKS5、缓存、连接池、Web 管理界面。
"""

import argparse
import base64
import binascii
import errno
import hmac
import json
import os
import random
import re
import select
import signal
import socket
import socketserver
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

BUF = 65536
MAX_LINE = 65536
MAX_HEADERS = 100
UPSTREAM_CONNECT_TIMEOUT = 10.0

# HTTP method 只允许 RFC 7230 的 tchar。挡住二进制垃圾：既不让控制字符进日志，
# 也不让畸形 method 被转发给上游。
METHOD_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,20}$")

# 逐跳头：转发给上游前必须剥除
HOP_BY_HOP = {
    "proxy-authorization",
    "proxy-authenticate",
    "proxy-connection",
    "connection",
    "keep-alive",
    "te",
    "trailer",
    "upgrade",
}


# --------------------------------------------------------------------------- 日志

class Logger:
    """每行立即 flush，同时写 stdout 和文件，加锁防并发交错。"""

    def __init__(self, path=None):
        self._lock = threading.Lock()
        self._fh = None
        if path:
            # 行缓冲 + 每行显式 flush，双保险
            self._fh = open(path, "a", encoding="utf-8", buffering=1)

    def __call__(self, msg):
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        with self._lock:
            try:
                sys.stdout.write(line + "\n")
                sys.stdout.flush()
            except Exception:
                pass
            if self._fh:
                try:
                    self._fh.write(line + "\n")
                    self._fh.flush()
                    os.fsync(self._fh.fileno())
                except Exception:
                    pass

    def close(self):
        with self._lock:
            if self._fh:
                try:
                    self._fh.close()
                except Exception:
                    pass
                self._fh = None


# --------------------------------------------------------------------------- 统计

class Stats:
    def __init__(self):
        self._lock = threading.Lock()
        self.reset()

    def reset(self):
        with self._lock:
            self.connect_count = 0
            self.http_count = 0
            self.with_auth_count = 0
            self.targets = {}
            self.errors = 0
            self.since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def record(self, kind, target, has_auth):
        with self._lock:
            if kind == "CONNECT":
                self.connect_count += 1
            else:
                self.http_count += 1
            if has_auth:
                self.with_auth_count += 1
            if target:
                self.targets[target] = self.targets.get(target, 0) + 1

    def record_error(self):
        with self._lock:
            self.errors += 1

    def snapshot(self):
        with self._lock:
            return {
                "connect_count": self.connect_count,
                "http_count": self.http_count,
                "with_auth_count": self.with_auth_count,
                "targets": dict(self.targets),
                "errors": self.errors,
                "since": self.since,
            }


# --------------------------------------------------------------------------- 请求记录

class Recorder:
    """
    观测页面用的请求记录环形缓冲。日志文件照写（事后追溯），这里是给页面轮询用的。
    只保留最近 MAX 条，长挂机几天也不会把内存吃掉。
    """

    MAX = 500

    def __init__(self):
        self._lock = threading.Lock()
        self._seq = 0
        self._items = []

    def add(self, kind, target, user, has_auth):
        with self._lock:
            self._seq += 1
            rec = {
                "id": self._seq,
                "time": time.strftime("%H:%M:%S"),
                "kind": kind,
                "target": target,
                "user": user,           # 只放用户名，密码永不进这里
                "auth": bool(has_auth),
                "ok": None,             # None=进行中 True=成功 False=失败
                "reason": "",
            }
            self._items.append(rec)
            if len(self._items) > self.MAX:
                del self._items[: len(self._items) - self.MAX]
            return rec

    @staticmethod
    def finish(rec, ok, reason=""):
        # rec 是上面 add 返回的同一个 dict 对象，原地改；读取侧是整体复制，够用了
        if rec is not None:
            rec["ok"] = ok
            rec["reason"] = reason

    def since(self, last_id):
        with self._lock:
            return [dict(r) for r in self._items if r["id"] > last_id]

    def clear(self):
        with self._lock:
            self._items = []


class Injection:
    """
    故障注入的运行时状态。页面上能实时切换，不用重启进程改参数。
    读的地方在各个连接线程里，写的地方只有页面接口，所以写加锁、读直接取。
    """

    def __init__(self, cfg):
        self._lock = threading.Lock()
        self.reject_all = cfg.reject_all
        self.auth_required = cfg.auth_required     # (user, pass) 或 None
        self.delay = cfg.delay
        # 这两个仍只能命令行设定，页面只读展示，免得控件太多点错
        self.fail_rate = cfg.fail_rate
        self.allow_host = list(cfg.allow_host)

    def apply(self, data, log):
        changed = []
        with self._lock:
            if "reject_all" in data:
                self.reject_all = bool(data["reject_all"])
                changed.append("拒绝所有请求=%s" % ("开" if self.reject_all else "关"))
            if "delay" in data:
                try:
                    self.delay = max(0.0, float(data["delay"] or 0))
                except (TypeError, ValueError):
                    self.delay = 0.0
                changed.append("延迟=%gs" % self.delay)
            if "auth_required" in data:
                raw = data["auth_required"]
                if not raw:
                    self.auth_required = None
                    changed.append("强制认证=关")
                else:
                    user, _, pwd = str(raw).partition(":")
                    self.auth_required = (user, pwd)
                    changed.append("强制认证=开(user=%s)" % sanitize(user, 40))
        if changed:
            log("  == 页面调整故障注入：%s" % "，".join(changed))
        return changed

    def snapshot(self):
        with self._lock:
            return {
                "reject_all": self.reject_all,
                "auth_user": self.auth_required[0] if self.auth_required else "",
                "auth_on": self.auth_required is not None,
                "delay": self.delay,
                "fail_rate": self.fail_rate,
                "allow_host": list(self.allow_host),
            }


# --------------------------------------------------------------------------- 工具函数

def errdesc(exc):
    """把异常转成测试人员看得懂的一句话。"""
    if isinstance(exc, socket.timeout):
        return "Connection timed out"
    if isinstance(exc, socket.gaierror):
        return "DNS 解析失败 (%s)" % (exc.strerror or exc,)
    if isinstance(exc, OSError):
        if exc.errno == errno.ECONNREFUSED:
            return "Connection refused"
        if exc.errno == errno.EHOSTUNREACH:
            return "No route to host"
        if exc.errno == errno.ENETUNREACH:
            return "Network is unreachable"
        if exc.errno == errno.ECONNRESET:
            return "Connection reset by peer"
        if exc.strerror:
            return exc.strerror
    return "%s: %s" % (type(exc).__name__, exc)


def sanitize(text, limit=200):
    """
    客户端可以往请求行里塞任意字节。日志是本工具的核心产出，使用者要 tail -f 看，
    所以凡是来自客户端的内容，落日志前一律：去控制字符（别把终端搞花）+ 截断（别让
    一个 80KB 的 URL 把日志文件冲了）。
    """
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("latin-1", "replace")
    cleaned = "".join(
        ch if (32 <= ord(ch) < 127) or ord(ch) > 159 else "." for ch in text)
    if len(cleaned) > limit:
        return cleaned[:limit] + "…(已截断，原长 %d 字符)" % len(text)
    return cleaned


def parse_proxy_auth(value):
    """
    解析 Proxy-Authorization: Basic base64(user:pass)。
    返回 (user, password)。解析不出来返回 (None, None)。
    调用方只允许把 user 写进日志，password 仅用于 --auth-required 比对。
    """
    if not value:
        return None, None
    parts = value.split(None, 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return None, None
    try:
        raw = base64.b64decode(parts[1].strip(), validate=True).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return None, None
    if ":" not in raw:
        return raw, ""
    user, pwd = raw.split(":", 1)
    return user, pwd


def split_hostport(netloc, default_port):
    """host:port -> (host, port)，兼容 IPv6 字面量 [::1]:8080。"""
    netloc = netloc.strip()
    if netloc.startswith("["):
        end = netloc.find("]")
        if end == -1:
            raise ValueError("非法地址: %s" % netloc)
        host = netloc[1:end]
        rest = netloc[end + 1:]
        port = int(rest[1:]) if rest.startswith(":") and rest[1:] else default_port
        return host, port
    if ":" in netloc:
        host, _, p = netloc.rpartition(":")
        if not host:
            raise ValueError("非法地址: %s" % netloc)
        return host, int(p)
    return netloc, default_port


class LineReader:
    """
    自己管缓冲的 socket 读取器。
    不用 socket.makefile 是因为切到裸 TCP 转发时，必须能把已经预读进缓冲区
    的字节原样交给上游（CONNECT 之后客户端可能已经把 TLS ClientHello 一起发过来了）。
    """

    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()
        self.eof = False

    def _fill(self):
        data = self.sock.recv(BUF)
        if not data:
            self.eof = True
            return False
        self.buf.extend(data)
        return True

    def read_line(self):
        while True:
            idx = self.buf.find(b"\n")
            if idx != -1:
                line = bytes(self.buf[: idx + 1])
                del self.buf[: idx + 1]
                return line
            if len(self.buf) > MAX_LINE:
                raise ValueError("请求行/头过长")
            if not self._fill():
                if self.buf:
                    line = bytes(self.buf)
                    self.buf.clear()
                    return line
                return b""

    def leftover(self):
        data = bytes(self.buf)
        self.buf.clear()
        return data


# --------------------------------------------------------------------------- 转发

def relay(sock_a, sock_b, idle_timeout, pre_to_b=b""):
    """
    双向实时转发，不缓冲完整响应体（SSE 流式输出必须能一直流）。
    任一端关闭 -> 半关另一端；空闲超过 idle_timeout -> 收工。
    """
    if pre_to_b:
        sock_b.sendall(pre_to_b)

    for s in (sock_a, sock_b):
        s.setblocking(True)
        s.settimeout(None)

    alive = [sock_a, sock_b]
    while alive:
        try:
            readable, _, errored = select.select(alive, [], alive, idle_timeout)
        except (OSError, ValueError):
            break
        if not readable and not errored:
            break  # 空闲超时
        if errored:
            break
        for s in readable:
            other = sock_b if s is sock_a else sock_a
            try:
                data = s.recv(BUF)
            except OSError:
                return
            if not data:
                if s in alive:
                    alive.remove(s)
                try:
                    other.shutdown(socket.SHUT_WR)
                except OSError:
                    pass
                continue
            try:
                other.sendall(data)
            except OSError:
                return


# --------------------------------------------------------------------------- 代理处理器

class ProxyHandler(socketserver.BaseRequestHandler):
    # 由 main() 注入
    cfg = None
    log = None
    stats = None
    recorder = None
    inject = None

    # -- 小工具 -------------------------------------------------------------
    def _send(self, raw):
        try:
            self.request.sendall(raw)
        except OSError:
            pass

    def _send_status(self, code, reason, extra_headers=(), body=b""):
        head = "HTTP/1.1 %d %s\r\n" % (code, reason)
        head += "Content-Length: %d\r\n" % len(body)
        head += "Connection: close\r\n"
        for k, v in extra_headers:
            head += "%s: %s\r\n" % (k, v)
        head += "\r\n"
        self._send(head.encode("latin-1") + body)

    # -- 主流程 -------------------------------------------------------------
    def handle(self):
        # 错误隔离：单连接的任何异常都不许影响其他连接或让进程退出
        try:
            self._handle()
        except Exception as exc:  # noqa: BLE001
            try:
                self.log("  !! 连接处理异常 %s — %s" % (self._peer(), errdesc(exc)))
                self.stats.record_error()
            except Exception:
                pass

    def _peer(self):
        try:
            return "%s:%d" % self.client_address[:2]
        except Exception:
            return "?"

    def _handle(self):
        cfg = self.cfg
        sock = self.request
        sock.settimeout(cfg.idle_timeout)

        reader = LineReader(sock)
        try:
            request_line = reader.read_line()
        except (OSError, ValueError) as exc:
            self.log("  !! 读取请求行失败 %s — %s" % (self._peer(), errdesc(exc)))
            return
        if not request_line.strip():
            return  # 空连接（探活/端口扫描），静默丢弃

        try:
            method, target, version = self._parse_request_line(request_line)
        except ValueError as exc:
            why = sanitize(str(exc), 120)
            self.log("  !! 请求行无法解析 %s — %s" % (self._peer(), why))
            # 也记一条：页面上能看出「有东西连进来了，但不是合法代理请求」
            rec = self.recorder.add("?", "(无法解析)", None, False)
            self.recorder.finish(rec, False, "请求行无法解析 — %s" % why)
            self.stats.record_error()
            self._send_status(400, "Bad Request", body=b"proxy_probe: bad request line\n")
            return

        headers = self._read_headers(reader)
        auth_user, auth_pwd = parse_proxy_auth(self._header(headers, "proxy-authorization"))
        has_auth = self._header(headers, "proxy-authorization") is not None
        authinfo = "with-auth user=%s" % auth_user if auth_user is not None else (
            "with-auth user=?" if has_auth else "no-auth"
        )

        is_connect = method.upper() == "CONNECT"
        if is_connect:
            # 关键：不对目标端口做任何假设。undici 打向 8080/28100 等明文端口也用 CONNECT。
            hostport = target
        else:
            try:
                parts = urlsplit(target)
                netloc = parts.netloc          # 非法 IPv6 字面量在这里才会炸
            except ValueError as exc:
                why = sanitize(str(exc), 120)
                self.log("%s %s (%s)" % (method.upper(), sanitize(target), authinfo))
                self.log("  !! 目标 URL 非法 — %s" % why)
                rec = self.recorder.add(method.upper(), sanitize(target, 80), auth_user, has_auth)
                self.recorder.finish(rec, False, "目标 URL 非法 — %s" % why)
                self.stats.record_error()
                self._send_status(400, "Bad Request", body=b"proxy_probe: bad target URL\n")
                return
            if not netloc:
                self.log("%s %s (%s) —— 非 absolute-URI，疑似直接把本工具当普通服务器访问" %
                         (method.upper(), sanitize(target), authinfo))
                rec = self.recorder.add(method.upper(), sanitize(target, 80), auth_user, has_auth)
                self.recorder.finish(rec, False,
                                     "不是 absolute-URI —— 这不是代理请求，"
                                     "疑似直接把本工具当普通服务器访问了")
                self._send_status(
                    400, "Bad Request",
                    body=b"proxy_probe: expected absolute-URI request (use me as a proxy)\n")
                return
            default_port = 443 if parts.scheme == "https" else 80
            hostport = netloc

        # 一条请求一行日志：形态 + 目标 + 认证状态
        self.log("%s %s (%s)" % (method.upper(), sanitize(target), authinfo))
        try:
            host, port = split_hostport(hostport, 443 if is_connect else default_port)
        except ValueError as exc:
            why = sanitize(str(exc), 120)
            self.log("  !! 目标地址非法 %s — %s" % (sanitize(hostport, 120), why))
            rec = self.recorder.add(method.upper(), sanitize(hostport, 80), auth_user, has_auth)
            self.recorder.finish(rec, False, "目标地址非法 — %s" % why)
            self.stats.record_error()
            self._send_status(400, "Bad Request", body=b"proxy_probe: bad target\n")
            return
        # host 保持原样用于连接；落日志/统计的键一律走 sanitize，防止畸形目标污染日志和 JSON
        target_key = sanitize("%s:%d" % (host, port), 120)
        self.stats.record("CONNECT" if is_connect else "HTTP", target_key, has_auth)
        rec = self.recorder.add(method.upper(), target_key, auth_user, has_auth)

        # ---- 故障注入（页面可实时切换，取值都从 inject 读）----
        inject = self.inject
        if inject.reject_all:
            self.log("  !! 故障注入 拒绝所有请求：立即断开 %s" % target_key)
            self.recorder.finish(rec, False, "故障注入「拒绝所有请求」— 已立即断开")
            self.stats.record_error()
            return  # 不回任何字节，直接关

        want = inject.auth_required
        if want:
            want_user, want_pwd = want
            ok = (auth_user is not None
                  and hmac.compare_digest(auth_user, want_user)
                  and hmac.compare_digest(auth_pwd or "", want_pwd))
            if not ok:
                why = "缺少凭证" if not has_auth else "凭证不匹配 (user=%s)" % sanitize(auth_user, 40)
                self.log("  !! 认证失败 %s — %s，返回 407" % (target_key, why))
                self.recorder.finish(rec, False, "认证失败 — %s，已返回 407" % why)
                self.stats.record_error()
                self._send_status(
                    407, "Proxy Authentication Required",
                    extra_headers=[("Proxy-Authenticate", 'Basic realm="test"')],
                    body=b"proxy_probe: proxy authentication required\n")
                return

        if inject.allow_host and not self._host_allowed(host, target_key):
            self.log("  !! 目标不在 --allow-host 白名单内 %s，拒绝" % target_key)
            self.recorder.finish(rec, False, "目标不在 --allow-host 白名单内，已返回 403")
            self.stats.record_error()
            self._send_status(403, "Forbidden", body=b"proxy_probe: target not allowed\n")
            return

        if inject.fail_rate > 0 and random.random() < inject.fail_rate:
            self.log("  !! 故障注入 --fail-rate=%.2f：返回 502 %s" % (inject.fail_rate, target_key))
            self.recorder.finish(rec, False,
                                 "故障注入「随机失败」(fail-rate=%g) — 已返回 502" % inject.fail_rate)
            self.stats.record_error()
            self._send_status(502, "Bad Gateway", body=b"proxy_probe: injected failure\n")
            return

        delay = inject.delay
        if delay > 0:
            self.log("  .. 故障注入 延迟 %gs：延迟后再转发 %s" % (delay, target_key))
            time.sleep(delay)

        # ---- 连上游 ----
        try:
            upstream = socket.create_connection((host, port), UPSTREAM_CONNECT_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            why = errdesc(exc)
            self.log("  !! 连接上游失败 %s — %s" % (target_key, why))
            self.recorder.finish(rec, False,
                                 "连接上游失败 — %s（代理收到了请求，是转发这一步失败）" % why)
            self.stats.record_error()
            if is_connect:
                self._send_status(502, "Bad Gateway")
            else:
                self._send_status(502, "Bad Gateway",
                                  body=("proxy_probe: upstream unreachable %s — %s\n"
                                        % (target_key, errdesc(exc))).encode("utf-8"))
            return

        try:
            if is_connect:
                # 隧道：应答 200 后做裸 TCP 双向转发
                self._send(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                self.recorder.finish(rec, True, "隧道已建立，双向转发中")
                relay(sock, upstream, cfg.idle_timeout, pre_to_b=reader.leftover())
            else:
                # absolute-URI 转发：请求行改写成 origin-form，剥掉逐跳头
                head = self._rewrite_request(method, parts, version, headers, host, port)
                upstream.sendall(head)
                self.recorder.finish(rec, True, "已转发")
                # 请求体（Content-Length / chunked）和响应一起交给双向转发：
                # 不解析、不缓冲，SSE 流式响应也能一直流。
                relay(sock, upstream, cfg.idle_timeout, pre_to_b=reader.leftover())
        finally:
            for s in (upstream,):
                try:
                    s.close()
                except OSError:
                    pass

    # -- 解析 ---------------------------------------------------------------
    @staticmethod
    def _parse_request_line(raw):
        line = raw.decode("latin-1").strip()
        bits = line.split()
        if len(bits) == 2:  # HTTP/0.9 之类，补上版本
            bits.append("HTTP/1.1")
        if len(bits) != 3:
            raise ValueError("请求行不是 3 段: %s" % sanitize(line, 120))
        if not METHOD_RE.match(bits[0]):
            raise ValueError("method 非法: %s" % sanitize(bits[0], 40))
        return bits[0], bits[1], bits[2]

    def _read_headers(self, reader):
        headers = []
        while True:
            line = reader.read_line()
            if not line or line in (b"\r\n", b"\n"):
                break
            if len(headers) >= MAX_HEADERS:
                raise ValueError("请求头过多")
            text = line.decode("latin-1").rstrip("\r\n")
            if text[:1] in (" ", "\t") and headers:  # 折行续写
                headers[-1] = (headers[-1][0], headers[-1][1] + " " + text.strip())
                continue
            name, _, value = text.partition(":")
            headers.append((name.strip(), value.strip()))
        return headers

    @staticmethod
    def _header(headers, name):
        for k, v in headers:
            if k.lower() == name:
                return v
        return None

    @staticmethod
    def _host_allowed(host, target_key):
        for item in ProxyHandler.inject.allow_host:
            item = item.strip().lower()
            if item in (host.lower(), target_key.lower()):
                return True
        return False

    @staticmethod
    def _rewrite_request(method, parts, version, headers, host, port):
        # 请求行：absolute-URI -> origin-form，否则规范上游会回 400
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        out = ["%s %s %s" % (method, path, version)]
        seen_host = False
        for name, value in headers:
            low = name.lower()
            if low.startswith("proxy-") or low in HOP_BY_HOP:
                continue  # 逐跳头必须剥除（含 Proxy-Authorization / Proxy-Connection）
            if low == "host":
                seen_host = True
            out.append("%s: %s" % (name, value))
        if not seen_host:
            out.append("Host: %s" % parts.netloc)
        # 让上游用完即关：我们不解析响应体，靠上游关闭来界定一次转发的结束
        out.append("Connection: close")
        return ("\r\n".join(out) + "\r\n\r\n").encode("latin-1")


class ProxyServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True          # 并发：每连接一线程，绝不单连接串行
    request_queue_size = 128

    def handle_error(self, request, client_address):
        # 兜底：任何漏出来的异常都不许打死进程
        exc = sys.exc_info()[1]
        try:
            ProxyHandler.log("  !! 连接异常（已隔离）%s — %s" % (client_address, errdesc(exc)))
        except Exception:
            pass


# --------------------------------------------------------------------------- 统计接口

def make_stats_handler(stats, log, recorder=None, inject=None):
    class StatsHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "proxy_probe-stats"

        def _json(self, code, payload):
            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        # /api/* 是规范路径；不带前缀的 /stats、/reset 保留，兼容已有脚本
        HELP = {"error": "try GET /api/stats, GET /api/records, POST /api/reset"}

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/stats", "/api/stats"):
                self._json(200, stats.snapshot())
            elif path == "/api/records":
                since = 0
                if "?" in self.path:
                    from urllib.parse import parse_qs, urlparse
                    q = parse_qs(urlparse(self.path).query)
                    try:
                        since = int(q.get("since", ["0"])[0])
                    except ValueError:
                        since = 0
                self._json(200, {
                    "records": recorder.since(since) if recorder else [],
                    "stats": stats.snapshot(),
                    "injection": inject.snapshot() if inject else {},
                })
            else:
                self._json(404, self.HELP)

        def do_POST(self):
            path = self.path.split("?")[0]
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            if path in ("/reset", "/api/reset"):
                stats.reset()
                if recorder:
                    recorder.clear()
                log("  == /reset 计数已清零、记录已清空（打基线）")
                self._json(200, stats.snapshot())
            elif path == "/api/inject" and inject:
                try:
                    data = json.loads(body or b"{}")
                except ValueError:
                    self._json(400, {"error": "body 不是合法 JSON"})
                    return
                changed = inject.apply(data, log)
                self._json(200, {"changed": changed, "injection": inject.snapshot()})
            else:
                self._json(404, self.HELP)

        def log_message(self, fmt, *args):
            pass  # 统计接口自身的访问不污染代理日志

    return StatsHandler


# --------------------------------------------------------------------------- 入口

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="proxy_probe.py",
        description="HTTP 正向代理观测仪器：验证请求到底走没走代理。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python3 proxy_probe.py --log-file /tmp/proxy.log
  python3 proxy_probe.py --auth-required svc:secret123
  python3 proxy_probe.py --allow-host 10.0.0.100:28100
  python3 proxy_probe.py --fail-rate 0.3 --delay 2
""")
    p.add_argument("--host", default="0.0.0.0",
                   help="监听地址，默认 0.0.0.0（容器要能连过来，别改 127.0.0.1）")
    # 端口跟 testBench 的 28xxx 约定对齐（代理观测占 28900），大号段不容易被占
    p.add_argument("--port", type=int, default=28900, help="代理端口，默认 28900")
    p.add_argument("--log-file", default=None, help="日志文件路径（同时仍输出到 stdout）")
    p.add_argument("--stats-port", type=int, default=28901,
                   help="统计/JSON 接口端口，默认 28901；设为 0 关闭")
    p.add_argument("--idle-timeout", type=float, default=60.0,
                   help="空闲超时秒数，默认 60（大模型响应慢，别调太小）")
    # ---- 故障注入 ----
    p.add_argument("--reject-all", action="store_true", help="故障注入：所有请求立即断开")
    p.add_argument("--auth-required", metavar="user:pass", default=None,
                   help="故障注入：凭证缺失/错误时返回 407")
    p.add_argument("--delay", type=float, default=0.0, metavar="秒",
                   help="故障注入：转发前延迟")
    p.add_argument("--fail-rate", type=float, default=0.0, metavar="0~1",
                   help="故障注入：按概率随机返回 502")
    p.add_argument("--allow-host", action="append", default=[], metavar="host[:port]",
                   help="故障注入：只放行指定目标，其余 403（可重复）")

    cfg = p.parse_args(argv)
    if cfg.auth_required:
        if ":" not in cfg.auth_required:
            p.error("--auth-required 需要 user:pass 形式")
        u, _, pw = cfg.auth_required.partition(":")
        cfg.auth_required = (u, pw)
    if not 0.0 <= cfg.fail_rate <= 1.0:
        p.error("--fail-rate 必须在 0~1 之间")
    if cfg.idle_timeout < 60:
        sys.stderr.write("提示：--idle-timeout=%g 低于建议的 60 秒，长响应可能被误杀\n"
                         % cfg.idle_timeout)
    return cfg


def main(argv=None):
    cfg = parse_args(argv)
    log = Logger(cfg.log_file)
    stats = Stats()

    recorder = Recorder()
    inject = Injection(cfg)
    ProxyHandler.cfg = cfg
    ProxyHandler.log = log
    ProxyHandler.stats = stats
    ProxyHandler.recorder = recorder
    ProxyHandler.inject = inject

    try:
        proxy = ProxyServer((cfg.host, cfg.port), ProxyHandler)
    except OSError as exc:
        sys.stderr.write("代理端口 %s:%d 启动失败 — %s\n" % (cfg.host, cfg.port, errdesc(exc)))
        return 1

    stats_srv = None
    if cfg.stats_port:
        try:
            stats_srv = ThreadingHTTPServer(
                (cfg.host, cfg.stats_port),
                make_stats_handler(stats, log, recorder, inject))
            stats_srv.daemon_threads = True
        except OSError as exc:
            sys.stderr.write("统计端口 %s:%d 启动失败 — %s\n"
                             % (cfg.host, cfg.stats_port, errdesc(exc)))
            proxy.server_close()
            return 1

    log("== proxy_probe 启动：代理 %s:%d%s" % (
        cfg.host, cfg.port,
        "，统计 http://%s:%d/stats" % (cfg.host, cfg.stats_port) if stats_srv else "，统计接口已关闭"))
    log("== 空闲超时 %gs；日志文件 %s" % (cfg.idle_timeout, cfg.log_file or "（未配置）"))
    injected = []
    if cfg.reject_all:
        injected.append("reject-all")
    if cfg.auth_required:
        injected.append("auth-required(user=%s)" % cfg.auth_required[0])
    if cfg.delay:
        injected.append("delay=%gs" % cfg.delay)
    if cfg.fail_rate:
        injected.append("fail-rate=%g" % cfg.fail_rate)
    if cfg.allow_host:
        injected.append("allow-host=%s" % ",".join(cfg.allow_host))
    log("== 故障注入：%s" % (" ".join(injected) if injected else "无（纯观测模式）"))

    if stats_srv:
        threading.Thread(target=stats_srv.serve_forever, daemon=True,
                         name="stats").start()

    # 长挂机的工具，停的时候大概率是 kill <PID>（SIGTERM），不只是 Ctrl-C。
    # 两种都要走同一条收尾路径，把累计计数写进日志尾部。
    def _on_term(signum, _frame):
        raise KeyboardInterrupt(signum)

    signal.signal(signal.SIGTERM, _on_term)

    try:
        proxy.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt as exc:
        why = "SIGTERM (kill)" if exc.args and exc.args[0] == signal.SIGTERM else "Ctrl-C"
        log("== 收到 %s，退出" % why)
    finally:
        proxy.shutdown()
        proxy.server_close()
        if stats_srv:
            stats_srv.shutdown()
            stats_srv.server_close()
        snap = stats.snapshot()
        log("== 本次累计：CONNECT %d / HTTP %d / with-auth %d / 错误 %d"
            % (snap["connect_count"], snap["http_count"],
               snap["with_auth_count"], snap["errors"]))
        log.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
