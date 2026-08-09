"""把一次执行的失败证据打包给 CC（A5）。

为什么不直接把原始数据塞进 MCP 返回值：
- **截图是 base64 存 JSONB**，单张上限 500KB、最多 10 张 → 单行最大 ~6.7MB，
  塞进 JSON 返回值约 170 万 token。写得出来，跑不了。
  所以落成临时 PNG 文件返回**绝对路径**，CC 和平台同机，直接 Read 看图。
- **captured_requests 一次能有 70~150 条**。全量给过去，CC 的上下文被流水账占满，
  真正的线索反而淹了。所以给摘要 + 只展开"值得看的那几条"。

原则：给 CC 的是**证据**，不是**结论**。平台只附上自己的现象初判（failure_phenomenon），
归因由 CC 做，且它的结论要另走确认通道（红线 3）。
"""
from __future__ import annotations

import base64
import binascii
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# 截图落盘目录。同机可达，CC 用 Read 直接打开。
SHOT_DIR = Path(tempfile.gettempdir()) / "testbench_evidence"

MAX_SHOTS = 4
MAX_INTERESTING = 20
MAX_STDOUT_TAIL = 3000
# 证据截图的保留期。落在 tmp 下、给 CC 看完就没用了，但一次跑攒几百 KB，
# 不清理的话长跑会把 /tmp 撑满。
SHOT_TTL_SECONDS = 6 * 3600


def _sweep_old_shots() -> None:
    """顺手清过期截图。清理失败不影响出证据。"""
    try:
        import time
        cutoff = time.time() - SHOT_TTL_SECONDS
        for f in SHOT_DIR.glob("*.png"):
            if f.stat().st_mtime < cutoff:
                f.unlink(missing_ok=True)
    except Exception:  # noqa: BLE001
        logger.debug("清理过期证据截图失败", exc_info=True)


def _dump_screenshots(run) -> list[dict]:
    """base64 → 临时 PNG 文件，返回路径。失败不影响其余证据。"""
    shots = run.screenshots or []
    if not shots:
        return []
    out = []
    try:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
    except Exception:  # noqa: BLE001
        logger.exception("截图目录创建失败")
        return []
    _sweep_old_shots()
    # 失败截图通常在最后 —— 倒着取更可能拿到"挂的那一刻"
    for i, s in enumerate(list(shots)[-MAX_SHOTS:]):
        b64 = (s or {}).get("base64")
        if not b64:
            continue
        try:
            path = SHOT_DIR / f"{run.id}_{i}_{(s.get('name') or 'shot')[:40]}"
            if not str(path).endswith(".png"):
                path = path.with_suffix(".png")
            path.write_bytes(base64.b64decode(b64))
            out.append({"name": s.get("name"), "path": str(path)})
        except (binascii.Error, ValueError, OSError):
            logger.exception("截图落盘失败 run=%s idx=%s", run.id, i)
    return out


def _summarize_requests(reqs: list[dict] | None) -> dict:
    """流水账压成摘要 + 值得看的那几条。

    "值得看"= 非 2xx（出错了）或写操作（改了状态）。一次登录+CRUD 抓 70 多条，
    其中绝大多数是页面自己的 GET，对判断失败没有信息量。
    """
    reqs = reqs or []
    if not reqs:
        return {"total": 0, "byStatus": {}, "interesting": [],
                "note": "没有网络流量记录。执行超时被 kill 时 Playwright 来不及 flush HAR，属正常。"}

    by_status: dict[str, int] = {}
    interesting = []
    for r in reqs:
        st = r.get("status")
        bucket = f"{st // 100}xx" if isinstance(st, int) else "无响应"
        by_status[bucket] = by_status.get(bucket, 0) + 1
        is_write = (r.get("method") or "").upper() not in ("GET", "HEAD", "OPTIONS")
        is_bad = not isinstance(st, int) or st >= 400
        if is_bad or is_write:
            interesting.append({
                "startedAt": r.get("startedAt"),
                "method": r.get("method"),
                "url": r.get("url"),
                "status": st,
                "requestBody": r.get("requestBody"),
                "responseBody": (r.get("responseBody") or "")[:800] or None,
            })
    return {
        "total": len(reqs),
        "byStatus": by_status,
        "interesting": interesting[:MAX_INTERESTING],
        "note": "interesting = 非 2xx 或写操作。其余是页面自身的 GET，对判断失败没有信息量，已折叠。",
    }


def build(run) -> dict:
    """给 CC 的证据包。通过的执行也能调，只是没有失败线索。"""
    ev: dict = {
        "screenshots": _dump_screenshots(run),
        "requests": _summarize_requests(run.captured_requests),
    }
    if run.stdout:
        # 只给尾部 —— 报错和 traceback 都在结尾，开头是 pytest 的启动噪音
        ev["stdout_tail"] = run.stdout[-MAX_STDOUT_TAIL:]
    ev["how_to_read"] = (
        "screenshots[].path 用 Read 直接打开看图（和平台同机）。"
        "failure_phenomenon 是平台按确定性规则给的**现象**初判，不是归因——"
        "为什么挂由你判断，判完别直接改状态，走确认通道。"
    )
    return ev
