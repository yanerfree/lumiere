"""节点池测试 —— 起一组假的大模型节点 + 往网关发一批请求 + 统计每条落到了哪个节点。

用途：验「网关的节点池」负载均衡 / 会话粘性 / 故障摘除 / 健康检查是否按预期工作。

  · 假节点：每个占一个端口（29870 起），说 OpenAI 的 /v1/chat/completions 方言，
    可以单独切成「报错 / 变慢 / 只有 /ready 报 503 / 流式中途断」来模拟机器坏了。
    节点只在页面上点了才起，**不进 lifespan**（lifespan 那一串端口已经够多了）。
  · 发送：后端代发（浏览器直接发会被跨域挡），一批一个后台任务，页面轮询进度。
  · 归属判定：每条请求带一个唯一追踪号（`NP-<批次>-<序号>`），放在请求头
    `X-NodePool-Trace`，勾了「自动加编号」时也放进问题正文。假节点收到后按追踪号记账
    —— 网关不转发自定义头、又没勾编号时，退回看**节点自己在回答里报的名字**。
    两路都有时以「节点收到」为准，这样网关失败重试（A 报错、转给 B）也看得出来。
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import socket
import time
import uuid
from collections import deque
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger("node_pool")

BASE_PORT = 29870
MAX_PORT = 29899
MAX_NODES = MAX_PORT - BASE_PORT + 1

MODES = {
    "normal": "正常",
    "error": "报错(500)",
    "slow": "变慢",
    "ready_503": "仅 /ready 返回 503",
    "stream_break": "流式中途出错",
}

TRACE_HEADER = "x-nodepool-trace"
NODE_HEADER = "X-NodePool-Node"
TRACE_RE = re.compile(r"NP-[0-9a-f]{6}-\d{4}")
# 节点在回答正文里自报家门的格式；发送端靠它兜底判归属
ECHO_RE = re.compile(r"【节点:([^】]{1,40})】")

_BODY_LIMIT = 4096


def _clip(s: str, n: int = _BODY_LIMIT) -> str:
    return s if len(s) <= n else s[:n] + f"\n…（已截断，共 {len(s)} 字）"


# ════════════════════════ 假节点 ════════════════════════

class FakeNode:
    def __init__(self, node_id: str, name: str, port: int, pool: "NodePool"):
        self.id = node_id
        self.name = name
        self.port = port
        self.mode = "normal"
        self.slow_seconds = 5.0
        self.pool = pool
        self.received = 0        # 收到的对话请求数（不含健康检查）
        self.probes = 0          # 健康检查被问了几次
        self.last_probe_at: float | None = None
        self._server = None
        self._task: asyncio.Task | None = None
        self.start_error: str | None = None

    @property
    def running(self) -> bool:
        if self._server is None or self._task is None or self._task.done():
            return False
        return bool(getattr(self._server, "started", False))

    def to_dict(self, host: str) -> dict:
        return {
            "id": self.id, "name": self.name, "port": self.port,
            "url": f"http://{host}:{self.port}",
            "mode": self.mode, "slowSeconds": self.slow_seconds,
            "running": self.running, "startError": self.start_error,
            "received": self.received, "probes": self.probes,
            "lastProbeAt": self.last_probe_at,
        }

    # ── HTTP 应用 ──
    def _build_app(self) -> FastAPI:
        app = FastAPI(title=f"NodePool fake node {self.name}", docs_url=None, redoc_url=None, openapi_url=None)
        node = self

        def _probe(paths_503: bool) -> JSONResponse:
            node.probes += 1
            node.last_probe_at = time.time()
            if paths_503 and node.mode == "ready_503":
                return JSONResponse({"status": "not ready", "node": node.name}, status_code=503,
                                    headers={NODE_HEADER: node.id})
            return JSONResponse({"status": "ok", "node": node.name}, headers={NODE_HEADER: node.id})

        @app.get("/ready")
        @app.get("/readyz")
        async def ready():
            return _probe(True)

        @app.get("/health")
        @app.get("/healthz")
        @app.get("/v1/health")
        async def health():
            return _probe(False)

        @app.get("/v1/models")
        @app.get("/models")
        async def models():
            return {"object": "list", "data": [{"id": "nodepool-fake", "object": "model", "owned_by": node.name}]}

        @app.api_route("/{path:path}", methods=["POST", "PUT", "GET", "DELETE", "PATCH"])
        async def catch_all(request: Request, path: str):
            return await node._handle_chat(request, path)

        return app

    async def _handle_chat(self, request: Request, path: str):
        raw = await request.body()
        text = raw.decode("utf-8", errors="replace")
        try:
            body = json.loads(text) if text.strip() else {}
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        hdrs = {k: v for k, v in request.headers.items()}
        stream = bool(body.get("stream", False))
        self.received += 1
        rec = self.pool._record(self, request.method, "/" + path, hdrs, text, stream)

        if self.mode == "error":
            rec["status"] = 500
            return JSONResponse(
                {"error": {"message": f"{self.name} 模拟故障（节点池测试）", "type": "server_error"}},
                status_code=500, headers={NODE_HEADER: self.id})
        if self.mode == "slow":
            await asyncio.sleep(max(0.0, float(self.slow_seconds)))

        user_text = ""
        for m in reversed(body.get("messages") or []):
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                user_text = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                break
        answer = f"【节点:{self.name}】你好，我是{self.name}。收到：{user_text[:80]}"
        model = body.get("model") or "nodepool-fake"
        rid = "chatcmpl-np" + uuid.uuid4().hex[:12]
        rec["status"] = 200

        if not stream:
            return JSONResponse({
                "id": rid, "object": "chat.completion", "created": int(time.time()), "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": answer},
                             "finish_reason": "stop"}],
                "usage": {"prompt_tokens": max(1, len(user_text) // 2), "completion_tokens": len(answer) // 2,
                          "total_tokens": max(1, len(user_text) // 2) + len(answer) // 2},
            }, headers={NODE_HEADER: self.id})

        pieces = [answer[i:i + 6] for i in range(0, len(answer), 6)]
        breaking = self.mode == "stream_break"

        async def gen():
            def chunk(delta: dict, finish=None) -> bytes:
                return ("data: " + json.dumps({
                    "id": rid, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }, ensure_ascii=False) + "\n\n").encode()
            yield chunk({"role": "assistant", "content": ""})
            for i, p in enumerate(pieces):
                if breaking and i == 2:
                    rec["status"] = "断流"
                    # 抛出去 → uvicorn 不发结束块直接断连接，对面看到的就是「回答到一半没了」
                    raise RuntimeError("节点池测试：模拟流式中途断开")
                yield chunk({"content": p})
                await asyncio.sleep(0.02)
            yield chunk({}, "stop")
            yield b"data: [DONE]\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers={NODE_HEADER: self.id})

    # ── 起停 ──
    async def start(self) -> None:
        if self.running:
            return
        import uvicorn
        from app.services._mock_server_util import guarded_serve, unlatch_sse_shutdown
        unlatch_sse_shutdown()
        config = uvicorn.Config(self._build_app(), host="0.0.0.0", port=self.port, log_level="warning")
        server = uvicorn.Server(config)
        task = asyncio.create_task(guarded_serve(server, f"节点池假节点 {self.name}"))
        self._server, self._task = server, task
        for _ in range(50):
            if server.started:
                self.start_error = None
                logger.info("节点池假节点 %s 已启动 :%d", self.name, self.port)
                return
            if task.done():
                break
            await asyncio.sleep(0.1)
        self._server = self._task = None
        self.start_error = f"端口 {self.port} 起不来（可能被别的程序占了）"
        raise RuntimeError(self.start_error)

    async def stop(self) -> None:
        dead = self._server
        if dead is None:
            return
        dead.should_exit = True
        if self._task:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._task, timeout=5)
        self._server = self._task = None
        from app.services._mock_server_util import unlatch_sse_shutdown
        unlatch_sse_shutdown(dead)


def _port_free(port: int) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


# ════════════════════════ 节点池 ════════════════════════

class NodePool:
    def __init__(self):
        self.nodes: dict[str, FakeNode] = {}
        self._lock = asyncio.Lock()
        # 追踪号 → 依次被哪些节点收到（网关重试时会有多个）
        self._by_trace: dict[str, list[dict]] = {}
        self._trace_order: deque[str] = deque()
        self.run: SendRun | None = None

    def _record(self, node: FakeNode, method: str, path: str, headers: dict, text: str, stream: bool) -> dict:
        found = set(TRACE_RE.findall(text))
        for v in headers.values():
            found.update(TRACE_RE.findall(v))
        rec = {"nodeId": node.id, "nodeName": node.name, "at": time.time(), "method": method,
               "path": path, "stream": stream, "status": None,
               "headers": headers, "body": _clip(text)}
        for t in found:
            if t not in self._by_trace:
                self._trace_order.append(t)
                if len(self._trace_order) > 5000:
                    self._by_trace.pop(self._trace_order.popleft(), None)
            self._by_trace.setdefault(t, []).append(rec)
        return rec

    def hits(self, trace: str) -> list[dict]:
        return list(self._by_trace.get(trace, []))

    def node_name(self, ref: str) -> str | None:
        n = self.nodes.get(ref)
        if n:
            return n.name
        for n in self.nodes.values():
            if n.name == ref:
                return n.name
        return None

    async def apply(self, specs: list[dict]) -> list[str]:
        """按页面给的整份清单对齐：多的停掉、少的起来、改了的就地改（不重启）。返回起不来的说明。"""
        errors: list[str] = []
        async with self._lock:
            want_ids = [str(s.get("id") or "").strip() for s in specs]
            for nid in list(self.nodes):
                if nid not in want_ids:
                    await self.nodes.pop(nid).stop()
            used = {n.port for n in self.nodes.values()}
            for s in specs[:MAX_NODES]:
                nid = str(s.get("id") or "").strip() or uuid.uuid4().hex[:6]
                name = (str(s.get("name") or "").strip() or f"节点{nid}")[:30]
                node = self.nodes.get(nid)
                if node is None:
                    port = self._pick_port(s.get("port"), used)
                    if port is None:
                        errors.append(f"{name}：29870~29899 端口都被占了")
                        continue
                    used.add(port)
                    node = FakeNode(nid, name, port, self)
                    self.nodes[nid] = node
                node.name = name
                mode = s.get("mode") or "normal"
                node.mode = mode if mode in MODES else "normal"
                try:
                    node.slow_seconds = min(300.0, max(0.0, float(s.get("slowSeconds", node.slow_seconds))))
                except (TypeError, ValueError):
                    pass
                if not node.running:
                    try:
                        await node.start()
                    except RuntimeError as e:
                        errors.append(f"{name}：{e}")
        return errors

    def _pick_port(self, preferred, used: set[int]) -> int | None:
        try:
            p = int(preferred) if preferred else None
        except (TypeError, ValueError):
            p = None
        if p and BASE_PORT <= p <= MAX_PORT and p not in used and _port_free(p):
            return p
        for p in range(BASE_PORT, MAX_PORT + 1):
            if p not in used and _port_free(p):
                return p
        return None

    async def stop_all(self) -> None:
        async with self._lock:
            for n in list(self.nodes.values()):
                await n.stop()
            self.nodes.clear()

    def reset_counters(self) -> None:
        for n in self.nodes.values():
            n.received = 0
            n.probes = 0
            n.last_probe_at = None


# ════════════════════════ 发送 ════════════════════════

class SendRun:
    def __init__(self, pool: NodePool, cfg: dict):
        self.pool = pool
        self.cfg = cfg
        self.id = uuid.uuid4().hex[:6]
        self.total = int(cfg["count"])
        self.started_at = time.time()
        self.finished_at: float | None = None
        self.results: list[dict] = []
        self.done = 0
        self.cancelled = False
        self.task: asyncio.Task | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()

    # ── 组装一条请求 ──
    def build(self, i: int) -> tuple[dict, dict, str, str | None]:
        c = self.cfg
        trace = f"NP-{self.id}-{i:04d}"
        headers: dict[str, str] = {"Content-Type": "application/json"}
        for h in c.get("headers") or []:
            k = str(h.get("key") or "").strip()
            if k and h.get("enabled", True) and str(h.get("value") or "").strip():
                headers[k] = str(h.get("value") or "")
        headers["X-NodePool-Trace"] = trace

        question = c.get("userPrompt") or "你好"
        if c.get("autoNumber"):
            question = f"{question}（第 {i} 条，{trace}）"
        messages = []
        if (c.get("systemPrompt") or "").strip():
            messages.append({"role": "system", "content": c["systemPrompt"]})
        messages.append({"role": "user", "content": question})
        body: dict[str, Any] = {"model": c.get("model") or "nodepool-fake", "messages": messages,
                                "stream": bool(c.get("stream"))}

        mode = c.get("sessionMode") or "none"
        value = None
        if mode != "none":
            field = (c.get("sessionField") or "session_id").strip() or "session_id"
            base = c.get("sessionValue") or "会话"
            value = base if mode == "same" else f"{base}-{i:04d}"
            place = c.get("sessionPlace") or "uag_metadata"
            if place == "header":
                headers[field] = value
            elif place == "body_metadata":
                body["metadata"] = {field: value}
            else:  # x-uag-metadata 请求头，值是 JSON
                headers["x-uag-metadata"] = json.dumps({field: value})  # 中文转成 \uXXXX，请求头里只能放 ASCII
        return headers, body, trace, value

    def _encode_headers(self, headers: dict) -> dict:
        # 中文值的请求头 httpx 会直接抛错；按 HTTP 规矩转成 UTF-8 百分号编码再发
        from urllib.parse import quote
        out = {}
        for k, v in headers.items():
            try:
                v.encode("latin-1")
                out[k] = v
            except UnicodeEncodeError:
                out[k] = quote(v, safe="")
        return out

    async def _one(self, client, i: int) -> None:
        import httpx
        headers, body, trace, session_value = self.build(i)
        headers = self._encode_headers(headers)  # 详情里展示的就是真正发出去的样子
        res: dict[str, Any] = {
            "seq": i, "trace": trace, "status": None, "ok": False, "error": None,
            "durationMs": None, "answer": "", "echoNode": None, "headerNode": None,
            "sessionValue": session_value, "requestHeaders": headers, "requestBody": body, "responseBody": "",
        }
        t0 = time.monotonic()
        try:
            if body["stream"]:
                async with client.stream("POST", self.cfg["targetUrl"], headers=headers,
                                         json=body) as r:
                    res["status"] = r.status_code
                    res["headerNode"] = r.headers.get(NODE_HEADER)
                    raw, content, got_done = [], [], False
                    async for line in r.aiter_lines():
                        raw.append(line)
                        if line.startswith("data:"):
                            data = line[5:].strip()
                            if data == "[DONE]":
                                got_done = True
                                continue
                            with contextlib.suppress(Exception):
                                d = json.loads(data)
                                for ch in d.get("choices") or []:
                                    piece = (ch.get("delta") or {}).get("content")
                                    if piece:
                                        content.append(piece)
                                    if ch.get("finish_reason"):
                                        got_done = True
                    res["responseBody"] = _clip("\n".join(raw))
                    res["answer"] = "".join(content)
                    if r.status_code >= 400:
                        res["error"] = f"返回 {r.status_code}"
                    elif not got_done:
                        res["error"] = "流式回答没有正常结束"
                    else:
                        res["ok"] = True
            else:
                r = await client.post(self.cfg["targetUrl"], headers=headers, json=body)
                res["status"] = r.status_code
                res["headerNode"] = r.headers.get(NODE_HEADER)
                res["responseBody"] = _clip(r.text)
                with contextlib.suppress(Exception):
                    res["answer"] = r.json()["choices"][0]["message"]["content"] or ""
                if r.status_code >= 400:
                    res["error"] = f"返回 {r.status_code}"
                else:
                    res["ok"] = True
        except httpx.TimeoutException:
            res["error"] = "等太久超时了"
        except httpx.ConnectError as e:
            res["error"] = f"连不上发送地址（{e}）"
        except (httpx.RemoteProtocolError, httpx.ReadError):
            res["error"] = "回答到一半连接断了"
        except Exception as e:  # noqa: BLE001
            res["error"] = f"{e.__class__.__name__}: {e}"
        res["durationMs"] = int((time.monotonic() - t0) * 1000)
        m = ECHO_RE.search(res["answer"] or res["responseBody"] or "")
        res["echoNode"] = m.group(1) if m else None
        self.results.append(res)
        self.done += 1

    async def execute(self) -> None:
        import httpx
        conc = max(1, int(self.cfg.get("concurrency") or 1))
        timeout = float(self.cfg.get("timeoutSeconds") or 60)
        sem = asyncio.Semaphore(conc)
        limits = httpx.Limits(max_connections=conc, max_keepalive_connections=conc)
        async with httpx.AsyncClient(timeout=timeout, verify=False, limits=limits, trust_env=False) as client:
            async def guarded(i: int):
                async with sem:
                    if self.cancelled:
                        return
                    await self._one(client, i)
            await asyncio.gather(*(guarded(i) for i in range(1, self.total + 1)))
        self.finished_at = time.time()

    # ── 汇总 ──
    def snapshot(self, with_details: bool = True) -> dict:
        pool = self.pool
        rows = []
        per_node: dict[str, dict] = {}
        for n in pool.nodes.values():
            per_node[n.name] = {"node": n.name, "answered": 0, "received": 0}

        def bucket(name: str) -> dict:
            return per_node.setdefault(name, {"node": name, "answered": 0, "received": 0})

        failed = unknown = retried = 0
        for r in sorted(self.results, key=lambda x: x["seq"]):
            hits = pool.hits(r["trace"])
            via = [h["nodeName"] for h in hits]
            for name in via:
                bucket(name)["received"] += 1
            final = None
            if r["ok"]:
                ok_hits = [h for h in hits if h.get("status") == 200]
                if ok_hits:
                    final = ok_hits[-1]["nodeName"]
                elif r["echoNode"]:
                    final = r["echoNode"]
                elif r["headerNode"]:
                    final = pool.node_name(r["headerNode"])
                if final:
                    bucket(final)["answered"] += 1
                else:
                    unknown += 1
            else:
                failed += 1
            if len(via) > 1:
                retried += 1
            row = {k: r[k] for k in ("seq", "trace", "status", "ok", "error", "durationMs", "sessionValue")}
            row.update({"node": final, "via": via, "answer": _clip(r["answer"], 300)})
            if with_details:
                row.update({"requestHeaders": r["requestHeaders"], "requestBody": r["requestBody"],
                            "responseBody": r["responseBody"],
                            "nodeReceived": [{"node": h["nodeName"], "path": h["path"], "status": h["status"],
                                              "headers": h["headers"], "body": h["body"]} for h in hits]})
            rows.append(row)

        sticky = None
        if (self.cfg.get("sessionMode") or "none") == "same":
            answered_nodes = {r["node"] for r in rows if r["node"]}
            sticky = {"sameNode": len(answered_nodes) <= 1, "nodes": sorted(answered_nodes)}

        return {
            "id": self.id, "running": self.running, "cancelled": self.cancelled,
            "total": self.total, "done": self.done,
            "startedAt": self.started_at, "finishedAt": self.finished_at,
            "targetUrl": self.cfg.get("targetUrl"), "sessionMode": self.cfg.get("sessionMode") or "none",
            "perNode": list(per_node.values()),
            "failed": failed, "unknown": unknown, "retried": retried,
            "sticky": sticky, "rows": rows,
        }


node_pool = NodePool()


async def start_run(cfg: dict) -> SendRun:
    if node_pool.run and node_pool.run.running:
        raise RuntimeError("上一批还在发，等它发完或先点「停止」")
    run = SendRun(node_pool, cfg)
    node_pool.run = run

    async def _go():
        try:
            await run.execute()
        except Exception as e:  # noqa: BLE001
            logger.exception("节点池发送异常: %s", e)
            run.finished_at = time.time()

    run.task = asyncio.create_task(_go())
    return run
