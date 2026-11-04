"""节点池测试工具：假节点真起端口、发送端按追踪号认出是哪个节点接的。

不用网关：直接把发送地址指向某个假节点，归属必须恰好落在它身上；
切成「报错」后必须算失败、且这条仍记在它的「收到几次」里。
"""
import json

import httpx
import pytest

from app.services.node_pool_manager import NodePool, SendRun, TRACE_RE


def _cfg(url, **kw):
    base = {"targetUrl": url, "count": 3, "concurrency": 2, "stream": False, "sessionMode": "none",
            "userPrompt": "你好", "autoNumber": True, "headers": []}
    base.update(kw)
    return base


async def _run(pool, cfg):
    run = SendRun(pool, cfg)
    await run.execute()
    return run.snapshot()


@pytest.mark.asyncio
async def test_直连节点_归属和失败都认得出():
    pool = NodePool()
    try:
        errs = await pool.apply([{"id": "A", "name": "节点A"}, {"id": "B", "name": "节点B"}])
        assert errs == []
        a = pool.nodes["A"]
        url = f"http://127.0.0.1:{a.port}/v1/chat/completions"

        s = await _run(pool, _cfg(url))
        per = {r["node"]: r for r in s["perNode"]}
        assert per["节点A"]["answered"] == 3 and per["节点B"]["received"] == 0
        assert s["failed"] == 0 and s["unknown"] == 0

        s = await _run(pool, _cfg(url, stream=True))
        assert {r["node"] for r in s["rows"]} == {"节点A"} and s["failed"] == 0

        await pool.apply([{"id": "A", "name": "节点A", "mode": "error"}, {"id": "B", "name": "节点B"}])
        s = await _run(pool, _cfg(url))
        per = {r["node"]: r for r in s["perNode"]}
        assert s["failed"] == 3 and per["节点A"]["answered"] == 0 and per["节点A"]["received"] == 3

        await pool.apply([{"id": "A", "name": "节点A", "mode": "stream_break"}, {"id": "B", "name": "节点B"}])
        s = await _run(pool, _cfg(url, stream=True, count=1))
        assert s["failed"] == 1 and "断" in s["rows"][0]["error"]
    finally:
        await pool.stop_all()


@pytest.mark.asyncio
async def test_仅ready报503_对话照常():
    pool = NodePool()
    try:
        await pool.apply([{"id": "A", "name": "节点A", "mode": "ready_503"}])
        port = pool.nodes["A"].port
        async with httpx.AsyncClient(trust_env=False) as c:
            assert (await c.get(f"http://127.0.0.1:{port}/ready")).status_code == 503
            assert (await c.get(f"http://127.0.0.1:{port}/health")).status_code == 200
        assert pool.nodes["A"].probes == 2
        s = await _run(pool, _cfg(f"http://127.0.0.1:{port}/v1/chat/completions", count=1))
        assert s["rows"][0]["ok"] and s["rows"][0]["node"] == "节点A"
    finally:
        await pool.stop_all()


def test_会话键三种放法_中文不炸请求头():
    pool = NodePool()
    for place in ("uag_metadata", "body_metadata", "header"):
        run = SendRun(pool, _cfg("http://x/v1/chat/completions", sessionMode="each", sessionPlace=place,
                                 sessionField="sid", sessionValue="会话"))
        headers, body, trace, value = run.build(7)
        assert value == "会话-0007" and TRACE_RE.fullmatch(trace)
        sent = run._encode_headers(headers)
        for v in sent.values():
            v.encode("latin-1")   # 真发出去的请求头必须全是 ASCII，否则 httpx 当场抛错
        if place == "uag_metadata":
            assert json.loads(sent["x-uag-metadata"]) == {"sid": "会话-0007"}
        elif place == "body_metadata":
            assert body["metadata"] == {"sid": "会话-0007"}
        else:
            assert "sid" in sent
        assert trace in body["messages"][-1]["content"]


def test_空值请求头不发():
    run = SendRun(NodePool(), _cfg("http://x", headers=[{"key": "Authorization", "value": ""},
                                                         {"key": "X-K", "value": "v", "enabled": False}]))
    headers, *_ = run.build(1)
    assert "Authorization" not in headers and "X-K" not in headers
