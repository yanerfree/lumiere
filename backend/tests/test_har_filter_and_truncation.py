"""执行期流量：dev server 噪声不许吃掉配额，截断必须留痕。

实测经过（TC-FWGL-00001 一次 UI 执行）：
- 抓到 150 条，其中 **102 条是 `/src/i18n/locales/*.json?import`**（全是 304）。
  两道防线都没拦住：扩展名名单里没有 `json`（加进去会误伤 /api/v1/config.json 这种
  真接口），而 **304 响应没有 content.mimeType**，MIME 那道在它们身上压根不触发。
- 代价不是"多几行噪声"：它们把 MAX_ENTRIES 吃光，于是本次执行**最关键的
  `POST /services/{id}/publish` 被截断丢掉了**。流量只覆盖前 7.3s，整轮跑了 13.4s。
  而这个面板的用途正是让人从流量里勾选接口、编排成接口场景。
- 面板显示「抓到 150 条请求」，150 恰好是上限，人读成"这次发了 150 条"。
  每次都是 150，数字自洽，所以这个谎一直没被发现。

所以两件事：按 dev server 的取源路径+模块查询串拦（不碰扩展名，避免误伤），
截断时留一条标记让调用方能说清"这份流量不全"。
"""
from __future__ import annotations

import json
from pathlib import Path

from app.engine.har import (
    _DEV_TOOLING_RE,
    _STATIC_EXT_RE,
    MAX_ENTRIES,
    parse_har,
    truncation_marker,
)


def _blocked(url: str) -> bool:
    return bool(_STATIC_EXT_RE.search(url) or _DEV_TOOLING_RE.search(url))


# ── 一、该拦的拦住 ──────────────────────────────────────────────

def test_vite_i18n_json_模块要拦():
    """这就是吃掉 102 个配额的那一类。"""
    assert _blocked("http://h:5176/src/i18n/locales/zh-CN/common.json?import")
    assert _blocked("http://h:5176/src/i18n/locales/zh-CN/nav.json?import")


def test_vite_源码模块要拦():
    assert _blocked("http://h:5176/src/App.jsx?t=1712345")
    assert _blocked("http://h:5176/src/pages/cases/CaseDetail.jsx")
    assert _blocked("http://h:5176/@vite/client")
    assert _blocked("http://h:5176/node_modules/.vite/deps/antd.js")


# ── 二、不该拦的一条都不许误伤 ────────────────────────────────────

def test_真业务接口不许被拦():
    for u in ["http://h:5176/api/v1/services?page=1",
              "http://h:5176/api/v1/services/abc-123/publish",
              "http://h:5176/api/v1/my-tenant",
              "http://h:5176/api/auth/login"]:
        assert not _blocked(u), u


def test_返回json的业务接口不许被拦():
    """为什么不能把 `json` 塞进扩展名名单 —— 会误伤这种。"""
    assert not _blocked("http://h:5176/api/v1/config.json")
    assert not _blocked("http://h:5176/v1/gateway/manifest.json")


def test_被测系统自己的_src_不在根路径下时的边界():
    """`/src/` 只在 dev server 取源码时出现。被测系统真有个 /api/src/ 资源的话
    会被误伤 —— 记下来：宁可漏掉这种极少数，也不能让 100+ 噪声吃掉配额。"""
    assert _blocked("http://h:5176/api/src/thing")   # 已知取舍，不是遗漏


# ── 三、截断必须留痕 ────────────────────────────────────────────

def _write_har(tmp_path: Path, n: int) -> Path:
    entries = [{
        "startedDateTime": f"2026-08-14T04:38:{i % 60:02d}.000Z", "time": 1,
        "request": {"method": "GET", "url": f"http://h:5176/api/v1/thing/{i}", "headers": []},
        "response": {"status": 200, "content": {"mimeType": "application/json", "text": "{}"}},
    } for i in range(n)]
    p = tmp_path / "network.har"
    p.write_text(json.dumps({"log": {"entries": entries}}), encoding="utf-8")
    return p


def test_没超上限时不留标记(tmp_path):
    out = parse_har(_write_har(tmp_path, 10))
    assert len(out) == 10
    assert truncation_marker(out) is None


def test_超上限时留下标记且说清丢了多少(tmp_path):
    total = MAX_ENTRIES + 37
    out = parse_har(_write_har(tmp_path, total))
    m = truncation_marker(out)
    assert m is not None, "截断了却没留痕 —— 面板会把上限当成真实条数"
    assert m["kept"] == MAX_ENTRIES
    assert m["totalSeen"] == total, f"要说清原始有多少条，实际 {m.get('totalSeen')}"


def test_标记不会被当成一条真实请求(tmp_path):
    """它没有 url/method，渲染成一行请求会很怪；调用方该用 truncation_marker 取。"""
    out = parse_har(_write_har(tmp_path, MAX_ENTRIES + 5))
    m = truncation_marker(out)
    assert m["url"] == "" and m["method"] == "" and m["status"] is None


def test_噪声不再吃配额_真实回放(tmp_path):
    """102 条噪声 + 48 条真请求，上限设成 60 —— 过滤到位的话一条真请求都不该丢。"""
    noise = [{
        "startedDateTime": "2026-08-14T04:38:01.000Z", "time": 1,
        "request": {"method": "GET", "url": f"http://h:5176/src/i18n/locales/zh-CN/f{i}.json?import",
                    "headers": []},
        "response": {"status": 304, "content": {}},      # 304 没有 mimeType，这是关键
    } for i in range(102)]
    real = [{
        "startedDateTime": "2026-08-14T04:38:08.000Z", "time": 1,
        "request": {"method": "POST", "url": f"http://h:5176/api/v1/services/{i}/publish",
                    "headers": []},
        "response": {"status": 200, "content": {"mimeType": "application/json", "text": "{}"}},
    } for i in range(48)]
    p = tmp_path / "network.har"
    # 噪声在前、真请求在后 —— 这正是当初 publish 被丢掉的顺序
    p.write_text(json.dumps({"log": {"entries": noise + real}}), encoding="utf-8")

    out = parse_har(p)
    assert truncation_marker(out) is None, "48 条真请求远低于上限，不该截断"
    urls = [e["url"] for e in out]
    assert len(out) == 48, f"应只剩 48 条真请求，实际 {len(out)}"
    assert all("/api/v1/services/" in u for u in urls)
    assert any("publish" in u for u in urls), "publish 必须还在 —— 这是当初丢掉的那条"
