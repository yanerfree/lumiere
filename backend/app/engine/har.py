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
#
# ⚠ 这份名单漏一类，「接口视图」就废了：实测一次登录+建项目抓到 75 条，
# 其中 **68 条是前端源码模块**（`/src/App.jsx?t=…`、`/@vite/client`），
# 真接口只有 7 条。人要在 75 行里挑出那 7 行，等于没给。
# 漏的两处：`.jsx/.tsx/.ts/.vue` 不在扩展名里；dev server 发的是
# `text/javascript`，而 mime 名单只有 `application/javascript`。
_STATIC_EXT_RE = re.compile(
    r"\.(?:png|jpe?g|gif|svg|webp|ico|bmp|avif|css|less|scss"
    r"|js|mjs|cjs|jsx|ts|tsx|mts|cts|vue|svelte|map"
    r"|woff2?|ttf|eot|otf|mp4|webm|mp3|wav|wasm)(?:\?|$)",
    re.I,
)
_STATIC_MIME_RE = re.compile(
    r"^(?:image|font|video|audio)/"
    r"|^text/(?:css|javascript|ecmascript)"
    r"|^application/(?:javascript|ecmascript|x-javascript|font-)",
    re.I,
)
# 开发服务器自己的东西：没有扩展名，mimeType 也常常是 text/javascript 之外的花样。
# 被测系统跑在 vite/webpack dev 上时，这些能占到抓包的一大半。
#
# ⚠ 第二次漏：`/src/i18n/locales/zh-CN/common.json?import` 这一类**一条没拦住**。
# 实测一次 UI 执行抓到 150 条，其中 **102 条是它们**（全是 304）。两处都失效：
#   · 扩展名名单里没有 `json`，而加进去会误伤真接口（/api/v1/config.json）
#   · **304 响应没有 content.mimeType**，所以 MIME 那道防线在它们身上压根不触发
# 代价不是"多几行噪声"：它们把 MAX_ENTRIES 的配额吃光，于是这次执行**最关键的
# 那条 `POST /services/{id}/publish` 被截断丢掉了** —— 而面板的用途正是让人从
# 这份流量里勾选接口编排成场景。流量只覆盖了前 7.3s，整轮跑了 13.4s。
#
# 所以改按「dev server 的取源路径 + 构建工具的查询串」拦，不碰扩展名：
#   /src/... 是 vite 直接吐源码的路径；`?import` / `?t=` / `?v=` 是它的模块查询串。
_DEV_TOOLING_RE = re.compile(
    r"/@(?:vite|react-refresh|id|fs|vite-plugin)|/node_modules/"
    r"|/__vite|/sockjs-node|hot-update|/__webpack|/_next/static|/@hmr"
    r"|/src/"                                   # vite dev 的源码目录
    r"|[?&](?:import|t|v)=(?:&|$)|[?&]import(?:&|$)",   # 模块查询串
    re.I,
)
# HMR 的 websocket：`ws://host:5173/?token=xxx`，握手返回 101。
# 只认**根路径**上的升级请求 —— 被测系统自己的 websocket 有具体路径
# （/ws、/socket.io…），不能一刀切把 101 全滤掉，那会把实时功能的证据也丢了。
_HMR_SOCKET_RE = re.compile(r"^wss?://[^/]+/(?:\?|$)", re.I)

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
        status = resp.get("status")
        if (_STATIC_EXT_RE.search(url) or _STATIC_MIME_RE.search(mime)
                or _DEV_TOOLING_RE.search(url)
                or (status == 101 and _HMR_SOCKET_RE.match(url))):
            continue
        post = req.get("postData") or {}
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
    total_seen = len(out)
    if total_seen <= MAX_ENTRIES:
        return out

    # ── 超额了：按重要性留，不是按先来后到留 ──
    #
    # 原来是「取前 150 条然后 break」。后果实测过：TC-FWGL-00004 一次执行 2960 条，
    # 配额全被页面自身的轮询 GET 吃光，**真正的写操作一条都没进来**，
    # 而拿这份流量去编排接口场景恰恰只需要那些写操作。丢掉的还都是时间上更靠后的
    # （publish 就这么丢过一次）。
    #
    # 优先级：写操作和非 2xx 全留（它们是编排的素材和排错的证据），
    # 剩下的配额给页面自身的 GET，**留最靠后的那些** —— 出问题时人要看的是
    # 失败前后那一段，不是刚进页面时的一串加载请求。
    def _important(e: dict) -> bool:
        st = e.get("status")
        return (e.get("method", "").upper() != "GET") or st is None or st >= 300

    important = [e for e in out if _important(e)]
    ordinary = [e for e in out if not _important(e)]
    quota = max(0, MAX_ENTRIES - len(important))
    kept_rows = important + (ordinary[-quota:] if quota else [])
    # 重新按时间排回去 —— 上面按重要性分了组，直接输出会让时间乱跳。
    kept_rows.sort(key=lambda e: e.get("startedAt") or "")
    dropped_ordinary = len(ordinary) - (quota if quota < len(ordinary) else len(ordinary))
    dropped_important = max(0, len(important) - MAX_ENTRIES)
    kept_rows = kept_rows[:MAX_ENTRIES]

    # **截断必须留痕。** 此前是静默 break，于是面板上写「抓到 150 条请求」——
    # 150 正好是上限，人读成"这次发了 150 条"，实际是"≥150，只留了前 150"。
    kept_rows.append({
        "startedAt": None, "elapsedMs": 0, "method": "", "url": "",
        "status": None, "requestHeaders": None, "requestBody": None,
        "responseBody": None,
        "truncated": True, "kept": len(kept_rows), "totalSeen": total_seen,
        "droppedOrdinary": dropped_ordinary, "droppedImportant": dropped_important,
    })
    logger.warning("HAR %s 条超过上限 %s：写操作/非 2xx 全留 %s 条，"
                   "丢弃页面自身的 GET %s 条（重要请求丢弃 %s 条）：%s",
                   total_seen, MAX_ENTRIES, len(important), dropped_ordinary,
                   dropped_important, p)
    return kept_rows


def truncation_marker(entries: list[dict] | None) -> dict | None:
    """取出截断标记（没截断就返回 None）。调用方拿它给用户说清"这份流量不全"。"""
    for e in reversed(entries or []):
        if isinstance(e, dict) and e.get("truncated"):
            return e
    return None


def har_path_for(output_dir: str | Path | None) -> str | None:
    """约定的 HAR 落点。跟截图共用 Playwright 的 output 目录，采集时机也跟着截图走——
    沙箱在 finally 里就被 rmtree 了，谁跟着 sandbox 谁拿不到。"""
    if not output_dir:
        return None
    return str(Path(output_dir) / "network.har")
