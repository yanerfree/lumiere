"""执行期网络流量采集（A3）——把 Playwright 录的 HAR 解析成结构化请求列表。

为什么要它：平台执行 UI 脚本时此前**不录任何网络流量**（`tea_capture` 插件 patch 的是
httpx，浏览器流量根本不经过它）。没有这份证据，失败分类只能读错误栈猜，CC 归因更是
纯瞎猜——"看起来很有道理的胡说"比没有分析更危险。

和 `services/ai/cli_agent.py:_parse_har` 的区别（那个是给「接口视图」用的，保留不动）：
1. **黑名单而不是白名单**。那边是 `if "/api/" not in url and "json" not in ct: continue`——
   被测系统前缀不是 /api/（比如 /v1/、/gateway/）会把 HAR 整个清空，分类器一律 unknown。
2. **body 字段级脱敏**。那边 requestBody 原样留 8000 字符，登录 POST 的明文密码会落库，
   再经 MCP 送进 CC 的上下文和日志。
3. **有绝对时间锚点和体积上限**。分类要按"失败时刻前若干秒"取窗口；没有上限的话
   300 条 × 16KB ≈ 4.8MB 一行。
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 静态资源黑名单：按扩展名和 mimeType 排除。剩下的一律保留——
# 不做 "/api/" 白名单，被测系统的接口前缀是什么我们不该假设。
_STATIC_EXT_RE = re.compile(
    r"\.(?:png|jpe?g|gif|svg|webp|ico|bmp|css|js|mjs|map|woff2?|ttf|eot|otf|mp4|webm|wasm)(?:\?|$)",
    re.I,
)
_STATIC_MIME_RE = re.compile(
    r"^(?:image|font|video|audio)/|^text/css|^application/(?:javascript|x-javascript|font-)",
    re.I,
)

_SECRET_KEY_RE = re.compile(
    r"(password|passwd|pwd|token|secret|api[_-]?key|authorization|auth|credential|cookie|session)",
    re.I,
)

MAX_BODY = 2000
MAX_ENTRIES = 150


def _mask_headers(headers: list[dict] | None) -> dict:
    out = {}
    for h in headers or []:
        k = h.get("name", "")
        out[k] = "***" if _SECRET_KEY_RE.search(k) else (h.get("value") or "")[:300]
    return out


def _mask_body(text: str | None, mime: str = "") -> str | None:
    """JSON 体按字段名脱敏；非 JSON 只截断。

    登录请求体里的明文密码会一路流到 script_runs → MCP → CC 的上下文和日志，
    这一层不盖住，后面每一层都盖不住了。
    """
    if not text:
        return None
    body = text[:MAX_BODY]

    def _regex_mask(t: str) -> str:
        # 兜底：截断的 JSON、form-urlencoded、任何解析不了的格式都要盖。
        # 早退不脱敏是不行的 —— 登录表单恰恰是 application/x-www-form-urlencoded。
        return re.sub(
            r'((?:password|passwd|pwd|token|secret|api[_-]?key|authorization)"?\s*[:=]\s*"?)[^"&,}\s]+',
            r'\1***', t, flags=re.I,
        )

    looks_json = "json" in (mime or "").lower() or body.lstrip().startswith(("{", "["))
    if not looks_json:
        return _regex_mask(body)
    try:
        data = json.loads(body)
    except Exception:  # noqa: BLE001
        return _regex_mask(body)

    def walk(o: Any, depth: int = 0) -> Any:
        if depth > 6:
            return o
        if isinstance(o, dict):
            return {k: ("***" if _SECRET_KEY_RE.search(str(k)) else walk(v, depth + 1))
                    for k, v in o.items()}
        if isinstance(o, list):
            return [walk(v, depth + 1) for v in o[:50]]
        return o

    return json.dumps(walk(data), ensure_ascii=False)[:MAX_BODY]


def parse_har(har_path: str | Path | None) -> list[dict]:
    """解析 HAR，返回按时间排序的请求列表（已滤静态资源、已脱敏、已截断）。

    拿不到就返回空列表——**超时被 kill 时 Playwright 来不及 flush HAR**
    （HAR 只在 context.close() 落盘），这是正常情况，不是错误。
    """
    if not har_path:
        return []
    p = Path(har_path)
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="ignore"))
    except Exception:  # noqa: BLE001
        logger.warning("HAR 解析失败（可能是执行超时被 kill，未 flush 完整）：%s", p)
        return []

    out: list[dict] = []
    for e in (data.get("log", {}) or {}).get("entries", []):
        req = e.get("request", {}) or {}
        resp = e.get("response", {}) or {}
        url = req.get("url", "")
        mime = ((resp.get("content", {}) or {}).get("mimeType") or "")
        if _STATIC_EXT_RE.search(url) or _STATIC_MIME_RE.search(mime):
            continue
        post = req.get("postData") or {}
        status = resp.get("status")
        # body 只在真正用得上的时候留：非 2xx（出错了要看返回了什么）或写操作
        # （要看提交了什么）。一次登录+CRUD 抓 70+ 条，全留 body 一行能到 300KB+，
        # 而这一行还要经 MCP 进 CC 的上下文。分类要的是 method/url/status/时间。
        keep_body = (status is None or status >= 300) or (req.get("method", "").upper() != "GET")
        out.append({
            "startedAt": e.get("startedDateTime"),      # 绝对时间锚点，分类按它取失败前的窗口
            "elapsedMs": int(e.get("time") or 0),
            "method": req.get("method", ""),
            "url": url,
            "status": status,
            "requestHeaders": _mask_headers(req.get("headers")) if keep_body else None,
            "requestBody": _mask_body(post.get("text"), post.get("mimeType", "")) if keep_body else None,
            "responseBody": _mask_body((resp.get("content", {}) or {}).get("text"), mime) if keep_body else None,
        })
        if len(out) >= MAX_ENTRIES:
            break
    return out


def har_path_for(output_dir: str | Path | None) -> str | None:
    """约定的 HAR 落点。跟截图共用 Playwright 的 output 目录，采集时机也跟着截图走——
    沙箱在 finally 里就被 rmtree 了，谁跟着 sandbox 谁拿不到。"""
    if not output_dir:
        return None
    return str(Path(output_dir) / "network.har")
