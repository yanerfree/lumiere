"""
HTTP 正向代理观测仪器 —— 后端监听器。

用途：验证被测系统「配了出站代理，请求是否真的走了代理」。
      页面上有记录 = 走了代理；没记录 = 没走代理（功能失效）。

支持两种代理协议形态，被测系统的两条出站链路各用一种，都必须支持：
  · CONNECT 隧道       —— Node.js / undici 走这条
  · absolute-URI 转发  —— Go net/http Transport 走这条

不做 TLS 中间人、不做 SOCKS5、不缓存、不解密内容 —— 只回答「有没有经过」。

与 tools/proxy_probe.py（可独立丢到测试机上跑的单文件版本）行为保持一致：
日志格式、统计字段、两种形态的处理方式都相同。
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import logging
import random
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

logger = logging.getLogger("proxy_probe")

_STATE_DIR = Path(__file__).resolve().parent.parent.parent / ".mock_state"
_STATE_FILE = _STATE_DIR / "proxy_probe.json"
_LOG_FILE = _STATE_DIR / "proxy_probe.log"

BUF = 65536
MAX_RECORDS = 500
PREVIEW_LIMIT = 4096      # 明细里请求体/响应体各只旁抄这么多字节，够看不吃内存
UPSTREAM_CONNECT_TIMEOUT = 10.0

# HTTP method 只允许 RFC 7230 的 tchar。挡住二进制垃圾：既不让控制字符进日志，
# 也不让畸形 method 被转发给上游。
METHOD_RE = re.compile(r"^[A-Za-z0-9!#$%&'*+.^_`|~-]{1,20}$")

# 逐跳头：转发给上游前必须剥除
HOP_BY_HOP = {
    "proxy-authorization", "proxy-authenticate", "proxy-connection",
    "connection", "keep-alive", "te", "trailer", "upgrade",
}


def sanitize(text: str | bytes, limit: int = 200, keep_newlines: bool = False) -> str:
    """
    客户端可以往请求行里塞任意字节。日志和页面是本工具的核心产出，
    所以凡是来自客户端的内容，落库前一律：去控制字符 + 截断。

    keep_newlines 只给「明细里的多行报文」用。**日志行必须保持默认 False** ——
    一条请求一行是日志的硬约定，放过换行就会被塞进假的日志行。
    """
    if isinstance(text, (bytes, bytearray)):
        text = bytes(text).decode("latin-1", "replace")
    allowed = {"\n", "\t"} if keep_newlines else set()
    cleaned = "".join(
        ch if (32 <= ord(ch) < 127) or ord(ch) > 159 or ch in allowed else "."
        for ch in text)
    if len(cleaned) > limit:
        return cleaned[:limit] + "…(已截断，原长 %d 字符)" % len(text)
    return cleaned


def parse_proxy_auth(value: str | None) -> tuple[str | None, str | None]:
    """
    解析 Proxy-Authorization: Basic base64(user:pass)。
    返回 (user, password)。两者都会存进记录给页面显示 —— base64 肉眼看不出内容，
    解开才能核对被测系统到底送了个什么凭证过来。
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


def split_hostport(netloc: str, default_port: int) -> tuple[str, int]:
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


def headers_text(request_line: str, headers) -> str:
    """
    把请求行 + 请求头拼成给页面看的文本。

    **原样显示，不做任何删改**（含 Proxy-Authorization 的完整值）——
    这是测试辅助工具，职责是如实呈现收到了什么；把内容改掉反而让人没法排查
    「被测系统到底送了个什么凭证过来」。
    """
    out = [request_line]
    for k, v in headers:
        out.append("%s: %s" % (sanitize(k, 80), sanitize(v, 2000)))
    return "\n".join(out)


def preview_text(raw: bytes, truncated: bool) -> str:
    """
    请求体/响应体预览。二进制或 TLS 加密内容不硬塞成乱码，直接说明是什么。
    """
    if not raw:
        return ""
    printable = sum(1 for b in raw[:512] if 9 <= b <= 13 or 32 <= b < 127)
    if printable < len(raw[:512]) * 0.85:
        return "（二进制或已加密内容，%d 字节，本工具不解密）" % len(raw)
    text = raw.decode("utf-8", "replace")
    return text + ("\n…（预览已截断）" if truncated else "")


def detect_lan_ip() -> str:
    """
    探测本机对外的内网 IP。

    页面必须把这个地址给使用者复制，不能给 127.0.0.1 ——
    请求方是 Docker 容器，容器里的 127.0.0.1 是容器自己，
    填错的表现是「代理日志永远为空」，会被误判成出站代理没生效。
    """
    import socket as _socket
    try:
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))       # 不发包，只为让内核选出口网卡
            return s.getsockname()[0]
        finally:
            s.close()
    except Exception:
        try:
            return _socket.gethostbyname(_socket.gethostname())
        except Exception:
            return ""


def errdesc(exc: BaseException) -> str:
    """把异常转成测试人员看得懂的一句话。"""
    import errno as _errno
    import socket as _socket
    if isinstance(exc, asyncio.TimeoutError):
        return "连接上游超时"
    if isinstance(exc, _socket.gaierror):
        return "DNS 解析失败 (%s)" % (exc.strerror or exc,)
    if isinstance(exc, ConnectionRefusedError):
        return "Connection refused"
    if isinstance(exc, OSError):
        mapping = {
            _errno.ECONNREFUSED: "Connection refused",
            _errno.EHOSTUNREACH: "No route to host",
            _errno.ENETUNREACH: "Network is unreachable",
            _errno.ECONNRESET: "Connection reset by peer",
        }
        if exc.errno in mapping:
            return mapping[exc.errno]
        if exc.strerror:
            return exc.strerror
    return "%s: %s" % (type(exc).__name__, exc)


class ProxyProbeManager:
    """
    代理监听器 + 观测数据。整个处理都在事件循环单线程里跑，
    所以 records / stats 不需要加锁。
    """

    def __init__(self) -> None:
        self.host: str = "0.0.0.0"          # 必须 0.0.0.0：请求方是容器，要连宿主机
        # 端口沿用各 mock 服务的 28xxx 约定（LLM 28100 / API 28200 / MCP 28300 /
        # WS 28400 / TCP 28500 / UDP 28600 / gRPC 28700 / OAuth2 28800），
        # 代理观测占 28900。用大号段是为了不容易被别的进程占掉。
        self.port: int = 28900
        self.idle_timeout: float = 60.0     # 大模型响应慢是常态，别调小
        # ---- 故障注入（页面可实时切换，不用重启）----
        self.reject_all: bool = False
        self.auth_required: tuple[str, str] | None = None
        self.delay: float = 0.0
        self.fail_rate: float = 0.0
        self.allow_host: list[str] = []
        # ---- 运行态 ----
        self._server: asyncio.AbstractServer | None = None
        self._serve_task: asyncio.Task | None = None
        self._records: list[dict] = []
        self._seq: int = 0
        self._log_fh = None
        self._reset_counters()

    # ------------------------------------------------------------------ 状态
    def _reset_counters(self) -> None:
        self.connect_count = 0
        self.http_count = 0
        self.with_auth_count = 0
        self.targets: dict[str, int] = {}
        self.errors = 0
        self.since = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    @property
    def running(self) -> bool:
        return self._server is not None and self._server.is_serving()

    @property
    def log_file(self) -> str:
        return str(_LOG_FILE)

    def _save_state(self, running: bool) -> None:
        try:
            _STATE_DIR.mkdir(parents=True, exist_ok=True)
            _STATE_FILE.write_text(json.dumps({"running": running, "port": self.port}))
        except Exception:
            pass

    def _load_state(self) -> bool:
        try:
            data = json.loads(_STATE_FILE.read_text())
            self.port = data.get("port", self.port)
            return bool(data.get("running", False))
        except Exception:
            return False

    # ------------------------------------------------------------------ 日志
    def _log(self, msg: str) -> None:
        """日志文件照写（事后追溯用），每行立即 flush。"""
        line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
        logger.info(msg)
        try:
            if self._log_fh is None:
                _STATE_DIR.mkdir(parents=True, exist_ok=True)
                self._log_fh = open(_LOG_FILE, "a", encoding="utf-8", buffering=1)
            self._log_fh.write(line + "\n")
            self._log_fh.flush()
        except Exception:
            pass

    # ------------------------------------------------------------------ 记录
    def _add_record(self, kind: str, target: str, user: str | None, has_auth: bool,
                    password: str | None = None, auth_raw: str | None = None) -> dict:
        self._seq += 1
        rec = {
            "id": self._seq,
            "time": time.strftime("%H:%M:%S"),
            "kind": kind,
            "target": target,
            "user": user,
            "auth": bool(has_auth),
            # 凭证原样保留：Proxy-Authorization 头的完整值 + 解码后的用户名/密码。
            # base64 肉眼看不出内容，所以解码结果也一并给出，方便核对被测系统送的对不对。
            "password": password,
            "auth_raw": auth_raw,
            "ok": None,            # None=进行中 True=成功 False=失败
            "reason": "",
            # ---- 明细（点开抽屉才取，不进列表轮询的返回体）----
            "raw_request": "",         # 客户端 -> 代理，原样（不做任何删改）
            "forwarded_request": "",   # 代理 -> 上游，改写后
            "stripped": [],            # 转发时剥掉的逐跳头
            "response_head": "",       # 上游 -> 客户端 的状态行 + 响应头
            "req_body": "",            # 请求体预览
            "resp_body": "",           # 响应体预览
        }
        self._records.append(rec)
        if len(self._records) > MAX_RECORDS:
            del self._records[: len(self._records) - MAX_RECORDS]
        return rec

    @staticmethod
    def _finish(rec: dict | None, ok: bool, reason: str = "") -> None:
        if rec is not None:
            rec["ok"] = ok
            rec["reason"] = reason

    # 列表轮询每秒一次，明细字段体积大，不跟着列表回传
    _DETAIL_KEYS = ("raw_request", "forwarded_request", "stripped",
                    "response_head", "req_body", "resp_body")

    def records_since(self, last_id: int = 0, limit: int = 200) -> list[dict]:
        """
        页面每秒轮询用：默认回最新 limit 条，整体替换列表。
        唯一键是这里的 id（不是时间戳 —— 同一秒可能有多条）。
        """
        items = [r for r in self._records if r["id"] > last_id]
        if limit > 0:
            items = items[-limit:]
        return [{k: v for k, v in r.items() if k not in self._DETAIL_KEYS} for r in items]

    def record_detail(self, rec_id: int) -> dict | None:
        for r in self._records:
            if r["id"] == rec_id:
                return dict(r)
        return None

    def stats(self) -> dict:
        return {
            "connect_count": self.connect_count,
            "http_count": self.http_count,
            "with_auth_count": self.with_auth_count,
            "targets": dict(self.targets),
            "errors": self.errors,
            "since": self.since,
        }

    def reset(self) -> None:
        """页面「清零」按钮：计数归零 + 列表清空。整个测试流程都依赖这个。"""
        self._reset_counters()
        self._records = []
        self._seq = 0
        self._log("  == 清零：计数归零、记录清空（打基线）")

    def injection(self) -> dict:
        return {
            "reject_all": self.reject_all,
            "auth_on": self.auth_required is not None,
            "auth_user": self.auth_required[0] if self.auth_required else "",
            "delay": self.delay,
            "fail_rate": self.fail_rate,
            "allow_host": list(self.allow_host),
        }

    def apply_injection(self, reject_all=None, auth_required=None, delay=None) -> list[str]:
        changed = []
        if reject_all is not None:
            self.reject_all = bool(reject_all)
            changed.append("拒绝所有请求=%s" % ("开" if self.reject_all else "关"))
        if delay is not None:
            try:
                self.delay = max(0.0, float(delay))
            except (TypeError, ValueError):
                self.delay = 0.0
            changed.append("延迟=%gs" % self.delay)
        if auth_required is not None:
            if not auth_required:
                self.auth_required = None
                changed.append("强制认证=关")
            else:
                user, _, pwd = str(auth_required).partition(":")
                self.auth_required = (user, pwd)
                changed.append("强制认证=开(user=%s)" % sanitize(user, 40))
        if changed:
            self._log("  == 页面调整故障注入：%s" % "，".join(changed))
        return changed

    # ------------------------------------------------------------------ 起停
    async def start(self) -> None:
        if self.running:
            return
        self._server = await asyncio.start_server(
            self._handle_client, host=self.host, port=self.port, backlog=128)
        self._log("== 代理监听启动 %s:%d（空闲超时 %gs）"
                  % (self.host, self.port, self.idle_timeout))
        logger.info("代理观测监听器已启动 %s:%d", self.host, self.port)
        self._save_state(True)

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            try:
                await asyncio.wait_for(self._server.wait_closed(), timeout=5)
            except Exception:
                pass
            self._server = None
            self._log("== 代理监听停止")
            logger.info("代理观测监听器已停止")
        self._save_state(False)

    # ------------------------------------------------------------------ 处理
    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        """错误隔离：单个连接的任何异常都不许影响其他连接或打死进程。"""
        try:
            await self._do_handle(reader, writer)
        except Exception as exc:  # noqa: BLE001
            try:
                self._log("  !! 连接处理异常 — %s" % errdesc(exc))
                self.errors += 1
            except Exception:
                pass
        finally:
            try:
                writer.close()
            except Exception:
                pass

    @staticmethod
    def _write_status(writer: asyncio.StreamWriter, code: int, reason: str,
                      extra_headers=(), body: bytes = b"") -> None:
        head = "HTTP/1.1 %d %s\r\n" % (code, reason)
        head += "Content-Length: %d\r\nConnection: close\r\n" % len(body)
        for k, v in extra_headers:
            head += "%s: %s\r\n" % (k, v)
        head += "\r\n"
        try:
            writer.write(head.encode("latin-1") + body)
        except Exception:
            pass

    async def _do_handle(self, reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter) -> None:
        # ---- 请求行 ----
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=self.idle_timeout)
        except (asyncio.TimeoutError, ValueError):
            return
        if not raw.strip():
            return  # 空连接（探活/端口扫描），静默丢弃

        try:
            method, target, version = self._parse_request_line(raw)
        except ValueError as exc:
            why = sanitize(str(exc), 120)
            self._log("  !! 请求行无法解析 — %s" % why)
            self._finish(self._add_record("?", "(无法解析)", None, False), False,
                         "请求行无法解析 — %s" % why)
            self.errors += 1
            self._write_status(writer, 400, "Bad Request", body=b"proxy_probe: bad request line\n")
            await self._drain(writer)
            return

        # ---- 请求头 ----
        headers = await self._read_headers(reader)
        raw_auth = self._header(headers, "proxy-authorization")
        auth_user, auth_pwd = parse_proxy_auth(raw_auth)
        has_auth = raw_auth is not None
        authinfo = ("with-auth user=%s" % auth_user) if auth_user is not None else (
            "with-auth user=?" if has_auth else "no-auth")

        is_connect = method.upper() == "CONNECT"
        parts = None
        if is_connect:
            # 关键：不对目标端口做任何假设。undici 打向 8080/28100 等明文端口也用 CONNECT。
            hostport = target
            default_port = 443
        else:
            try:
                parts = urlsplit(target)
                netloc = parts.netloc         # 非法 IPv6 字面量在这里才会炸
            except ValueError as exc:
                why = sanitize(str(exc), 120)
                self._log("%s %s (%s)" % (method.upper(), sanitize(target), authinfo))
                self._log("  !! 目标 URL 非法 — %s" % why)
                self._finish(self._add_record(method.upper(), sanitize(target, 80),
                                              auth_user, has_auth),
                             False, "目标 URL 非法 — %s" % why)
                self.errors += 1
                self._write_status(writer, 400, "Bad Request",
                                   body=b"proxy_probe: bad target URL\n")
                await self._drain(writer)
                return
            if not netloc:
                self._log("%s %s (%s) —— 非 absolute-URI，疑似直接把本工具当普通服务器访问"
                          % (method.upper(), sanitize(target), authinfo))
                self._finish(self._add_record(method.upper(), sanitize(target, 80),
                                              auth_user, has_auth),
                             False, "不是 absolute-URI —— 这不是代理请求，"
                                    "疑似直接把本工具当普通服务器访问了")
                self._write_status(
                    writer, 400, "Bad Request",
                    body=b"proxy_probe: expected absolute-URI request (use me as a proxy)\n")
                await self._drain(writer)
                return
            default_port = 443 if parts.scheme == "https" else 80
            hostport = netloc

        # 一条请求一行日志：形态 + 目标 + 认证状态
        self._log("%s %s (%s)" % (method.upper(), sanitize(target), authinfo))
        try:
            host, port = split_hostport(hostport, default_port)
        except ValueError as exc:
            why = sanitize(str(exc), 120)
            self._log("  !! 目标地址非法 %s — %s" % (sanitize(hostport, 120), why))
            self._finish(self._add_record(method.upper(), sanitize(hostport, 80),
                                          auth_user, has_auth),
                         False, "目标地址非法 — %s" % why)
            self.errors += 1
            self._write_status(writer, 400, "Bad Request", body=b"proxy_probe: bad target\n")
            await self._drain(writer)
            return

        target_key = sanitize("%s:%d" % (host, port), 120)
        if is_connect:
            self.connect_count += 1
        else:
            self.http_count += 1
        if has_auth:
            self.with_auth_count += 1
        self.targets[target_key] = self.targets.get(target_key, 0) + 1
        rec = self._add_record(method.upper(), target_key, auth_user, has_auth,
                               password=auth_pwd, auth_raw=raw_auth)
        # 原始请求（客户端 -> 代理）：原样留证，不删改
        rec["raw_request"] = headers_text(
            "%s %s %s" % (method.upper(), sanitize(target, 300), version), headers)

        # ---- 故障注入 ----
        if self.reject_all:
            self._log("  !! 故障注入 拒绝所有请求：立即断开 %s" % target_key)
            self._finish(rec, False, "故障注入「拒绝所有请求」— 已立即断开")
            self.errors += 1
            return  # 不回任何字节，直接关

        if self.auth_required:
            want_user, want_pwd = self.auth_required
            ok = (auth_user is not None
                  and hmac.compare_digest(auth_user, want_user)
                  and hmac.compare_digest(auth_pwd or "", want_pwd))
            if not ok:
                why = "缺少凭证" if not has_auth else "凭证不匹配 (user=%s)" % sanitize(auth_user, 40)
                self._log("  !! 认证失败 %s — %s，返回 407" % (target_key, why))
                self._finish(rec, False, "认证失败 — %s，已返回 407" % why)
                self.errors += 1
                self._write_status(
                    writer, 407, "Proxy Authentication Required",
                    extra_headers=[("Proxy-Authenticate", 'Basic realm="test"')],
                    body=b"proxy_probe: proxy authentication required\n")
                await self._drain(writer)
                return

        if self.allow_host and not self._host_allowed(host, target_key):
            self._log("  !! 目标不在白名单内 %s，拒绝" % target_key)
            self._finish(rec, False, "目标不在 allow-host 白名单内，已返回 403")
            self.errors += 1
            self._write_status(writer, 403, "Forbidden", body=b"proxy_probe: target not allowed\n")
            await self._drain(writer)
            return

        if self.fail_rate > 0 and random.random() < self.fail_rate:
            self._log("  !! 故障注入 随机失败 %.2f：返回 502 %s" % (self.fail_rate, target_key))
            self._finish(rec, False,
                         "故障注入「随机失败」(fail-rate=%g) — 已返回 502" % self.fail_rate)
            self.errors += 1
            self._write_status(writer, 502, "Bad Gateway", body=b"proxy_probe: injected failure\n")
            await self._drain(writer)
            return

        if self.delay > 0:
            self._log("  .. 故障注入 延迟 %gs：延迟后再转发 %s" % (self.delay, target_key))
            await asyncio.sleep(self.delay)

        # ---- 连上游 ----
        try:
            up_reader, up_writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=UPSTREAM_CONNECT_TIMEOUT)
        except Exception as exc:  # noqa: BLE001
            why = errdesc(exc)
            self._log("  !! 连接上游失败 %s — %s" % (target_key, why))
            self._finish(rec, False, "连接上游失败 — %s（代理收到了请求，是转发这一步失败）" % why)
            self.errors += 1
            body = b"" if is_connect else (
                "proxy_probe: upstream unreachable %s — %s\n" % (target_key, why)).encode()
            self._write_status(writer, 502, "Bad Gateway", body=body)
            await self._drain(writer)
            return

        try:
            if is_connect:
                writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                await writer.drain()
                self._finish(rec, True, "隧道已建立，双向转发中")
                rec["response_head"] = "HTTP/1.1 200 Connection Established"
                rec["forwarded_request"] = (
                    "（CONNECT 隧道不改写请求：代理只回一个 200 Connection Established，\n"
                    "之后在客户端与 %s 之间做裸 TCP 双向转发。\n"
                    "隧道内容通常是 TLS 加密的，本工具不做中间人、不解密 —— "
                    "只回答「有没有经过代理」。）" % target_key)
            else:
                head, stripped = self._rewrite_request(method, parts, version, headers)
                up_writer.write(head)
                await up_writer.drain()
                self._finish(rec, True, "已转发")
                # 转发给上游的报文原样留证：请求行是否改成了 origin-form、Proxy-* 是否剥掉了，
                # 都能在页面上直接看到，不用靠猜
                # 这里不用再打码：Proxy-Authorization 已经在改写时被剥掉了
                rec["forwarded_request"] = sanitize(
                    head.decode("latin-1").replace("\r\n", "\n").rstrip("\n"),
                    4000, keep_newlines=True)
                rec["stripped"] = stripped
            # 双向实时转发，不缓冲完整响应体（上游可能是 SSE 流式输出）
            await self._relay(reader, writer, up_reader, up_writer, rec, is_connect)
        finally:
            try:
                up_writer.close()
            except Exception:
                pass

    # ------------------------------------------------------------------ 细节
    @staticmethod
    async def _drain(writer: asyncio.StreamWriter) -> None:
        try:
            await writer.drain()
        except Exception:
            pass

    @staticmethod
    def _parse_request_line(raw: bytes) -> tuple[str, str, str]:
        line = raw.decode("latin-1").strip()
        bits = line.split()
        if len(bits) == 2:
            bits.append("HTTP/1.1")
        if len(bits) != 3:
            raise ValueError("请求行不是 3 段: %s" % sanitize(line, 120))
        if not METHOD_RE.match(bits[0]):
            raise ValueError("method 非法: %s" % sanitize(bits[0], 40))
        return bits[0], bits[1], bits[2]

    async def _read_headers(self, reader: asyncio.StreamReader) -> list[tuple[str, str]]:
        headers: list[tuple[str, str]] = []
        while len(headers) < 100:
            try:
                line = await asyncio.wait_for(reader.readline(), timeout=self.idle_timeout)
            except (asyncio.TimeoutError, ValueError):
                break
            if not line or line in (b"\r\n", b"\n"):
                break
            text = line.decode("latin-1").rstrip("\r\n")
            if text[:1] in (" ", "\t") and headers:      # 折行续写
                headers[-1] = (headers[-1][0], headers[-1][1] + " " + text.strip())
                continue
            name, _, value = text.partition(":")
            headers.append((name.strip(), value.strip()))
        return headers

    @staticmethod
    def _header(headers, name: str) -> str | None:
        for k, v in headers:
            if k.lower() == name:
                return v
        return None

    def _host_allowed(self, host: str, target_key: str) -> bool:
        for item in self.allow_host:
            if item.strip().lower() in (host.lower(), target_key.lower()):
                return True
        return False

    @staticmethod
    def _rewrite_request(method: str, parts, version: str, headers) -> tuple[bytes, list[str]]:
        """返回 (转发给上游的报文头, 被剥掉的逐跳头名字列表)。"""
        # 请求行：absolute-URI -> origin-form，否则规范上游会回 400
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        out = ["%s %s %s" % (method, path, version)]
        stripped: list[str] = []
        seen_host = False
        for name, value in headers:
            low = name.lower()
            if low.startswith("proxy-") or low in HOP_BY_HOP:
                stripped.append(name)   # 逐跳头必须剥除（含 Proxy-Authorization / Proxy-Connection）
                continue
            if low == "host":
                seen_host = True
            out.append("%s: %s" % (name, value))
        if not seen_host:
            out.append("Host: %s" % parts.netloc)
        # 让上游用完即关：本工具不解析响应体，靠上游关闭界定一次转发的结束，
        # 这样才能做到不缓冲、SSE 能一直流。
        out.append("Connection: close")
        return ("\r\n".join(out) + "\r\n\r\n").encode("latin-1"), stripped

    async def _relay(self, c_reader, c_writer, u_reader, u_writer,
                     rec: dict | None = None, is_connect: bool = False) -> None:
        """
        双向实时转发。顺带 tee 前 PREVIEW_LIMIT 字节做明细预览 ——
        **只旁抄、不缓冲**：数据照旧收到就立刻转出去，所以 SSE 仍然是边到边流的。
        """
        up_sink = {"buf": bytearray(), "cut": False}     # 客户端 -> 上游（请求体）
        down_sink = {"buf": bytearray(), "cut": False}   # 上游 -> 客户端（响应）

        async def pump(src, dst, sink):
            try:
                while True:
                    data = await asyncio.wait_for(src.read(BUF), timeout=self.idle_timeout)
                    if not data:
                        break
                    if len(sink["buf"]) < PREVIEW_LIMIT:
                        room = PREVIEW_LIMIT - len(sink["buf"])
                        sink["buf"] += data[:room]
                        if len(data) > room:
                            sink["cut"] = True
                    else:
                        sink["cut"] = True
                    dst.write(data)                     # 立刻转出，不等攒满
                    await dst.drain()
            except (asyncio.TimeoutError, asyncio.IncompleteReadError):
                pass
            except Exception:
                pass
            finally:
                try:
                    if dst.can_write_eof():
                        dst.write_eof()
                except Exception:
                    pass

        try:
            await asyncio.gather(pump(c_reader, u_writer, up_sink),
                                 pump(u_reader, c_writer, down_sink),
                                 return_exceptions=True)
        finally:
            if rec is not None:
                self._fill_preview(rec, up_sink, down_sink, is_connect)

    @staticmethod
    def _fill_preview(rec: dict, up_sink: dict, down_sink: dict, is_connect: bool) -> None:
        if is_connect:
            # 隧道内是裸 TCP（多数是 TLS）。本工具不做中间人，不解密，如实说明。
            rec["req_body"] = preview_text(bytes(up_sink["buf"]), up_sink["cut"])
            rec["resp_body"] = preview_text(bytes(down_sink["buf"]), down_sink["cut"])
            return
        rec["req_body"] = preview_text(bytes(up_sink["buf"]), up_sink["cut"])
        raw = bytes(down_sink["buf"])
        head, sep, body = raw.partition(b"\r\n\r\n")
        if sep:
            rec["response_head"] = sanitize(
                head.decode("latin-1", "replace").replace("\r\n", "\n"),
                4000, keep_newlines=True)
            rec["resp_body"] = preview_text(body, down_sink["cut"])
        else:
            rec["resp_body"] = preview_text(raw, down_sink["cut"])


proxy_probe = ProxyProbeManager()
