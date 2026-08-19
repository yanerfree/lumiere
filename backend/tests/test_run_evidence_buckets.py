"""网络证据摘要：被取消的请求既要说得明白，也不能从「值得看」里漏掉。

活体跑完一遍 UI 脚本时看到的：摘要里写着 `-1xx: 2`。
Playwright 把被取消/失败的请求记成 0 或 -1，而分桶是 `f"{st // 100}xx"` ——
-1 // 100 = -1，于是桶名就成了 `-1xx`。没人看得懂。

**比桶名更要紧的是漏报**：`is_bad = st >= 400` 判不到 -1/0，于是那几条被折叠进
"页面自身的 GET"里 —— 而"页面为什么没加载出来"的答案常常就在它们身上。
"""
from __future__ import annotations

from app.services.run_evidence_service import _summarize_requests as summarize


def _r(status, method="GET", url="/x"):
    return {"status": status, "method": method, "url": url}


def test_被取消的请求桶名说人话():
    s = summarize([_r(-1), _r(0), _r(200)])
    assert "无响应（被取消/失败）" in s["byStatus"]
    assert s["byStatus"]["无响应（被取消/失败）"] == 2
    assert not any(k.startswith("-") or k == "0xx" for k in s["byStatus"]), s["byStatus"]


def test_被取消的请求要进值得看():
    """它们是失败归因里信息量最大的几条，不能跟页面自身的 GET 一起折叠掉。"""
    s = summarize([_r(-1, url="/api/v1/services"), _r(200, url="/ok")])
    urls = [x["url"] for x in s["interesting"]]
    assert "/api/v1/services" in urls and "/ok" not in urls


def test_正常状态码照旧分桶():
    s = summarize([_r(200), _r(204), _r(302), _r(404), _r(500)])
    assert s["byStatus"] == {"2xx": 2, "3xx": 1, "4xx": 1, "5xx": 1}


def test_没有流量时说清为什么():
    s = summarize([])
    assert s["total"] == 0 and "HAR" in s["note"]
