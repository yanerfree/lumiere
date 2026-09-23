"""内置 Mock 套件的结构封样。

盯三件**错了不会当场报错**的事：

1. **同一「方法+路径」出现两条** —— 库上有唯一索引，启动播种会整个失败，
   而那是在 lifespan 里吞掉异常的（少几条示例是小事、起不来是大事），
   于是表现成「内置那组莫名其妙少了一半」。
2. **路径不是官方写法** —— 这套东西的卖点就是「填上就能用，不用改」。
   一旦有人图省事写成 `/mock/xxx`，SDK 拼出来的地址就对不上，
   而对不上的症状是 404，看着像 mock 服务没起。
3. **故障组配成了 200** —— 拿它当上游测重试/降级会**全绿**，
   而那种绿是假的：被测网关根本没进过错误分支。
"""
from __future__ import annotations

from app.services.llm_mock_builtin import (
    CATEGORY_LABELS,
    _BEHAVIOUR_DEFAULTS,
    builtin_specs,
)

# 各家官方的地址尾巴。场景变体只许在**前面**加前缀（/429/v1/chat/completions），
# 不许改尾巴 —— 尾巴一改，SDK 的 base_url 拼接就对不上了。
OFFICIAL_TAILS = (
    "/v1/chat/completions",
    "/v1/completions",
    "/v1/responses",
    "/v1/embeddings",
    "/v1/models",
    "/v1/messages",
    ":generateContent",
    ":streamGenerateContent",
    "/api/chat",
    "/api/generate",
    "/chat/completions",   # Azure：/openai/deployments/{model}/chat/completions
)


def test_方法加路径不重复():
    seen: dict[tuple[str, str], str] = {}
    for s in builtin_specs():
        key = (s["method"].upper(), s["path"])
        assert key not in seen, f"{key} 撞了：{seen[key]} 和 {s['name']}"
        seen[key] = s["name"]


def test_路径都是官方写法():
    for s in builtin_specs():
        p = s["path"]
        assert p.startswith("/"), p
        assert any(p.endswith(t) or t in p for t in OFFICIAL_TAILS), f"{p} 不是任何一家的官方地址"


def test_每条都说得清自己是干什么的():
    for s in builtin_specs():
        assert s["builtin"] is True
        assert s["locked"] is True, f"{s['path']} 没锁：内置的默认锁定，避免被顺手改掉"
        assert s["category"] in CATEGORY_LABELS, f"{s['path']} 的分组 {s['category']} 不在分组表里"
        assert s["name"].strip()
        assert s["purpose"].strip(), f"{s['path']} 没写用途"
        assert s["usage_hint"].strip(), f"{s['path']} 没写怎么用"


def test_故障组真的会报错():
    """故障与限流那一组，必须真的返回非 2xx（或者真的慢）。
    配成 200 的话，被测网关的重试/降级分支一次都不会走到，而用例照样全绿。"""
    errors = [s for s in builtin_specs() if s["category"] == "error"]
    assert errors
    for s in errors:
        bad = s.get("status_code", 200) >= 400
        slow = s.get("delay_ms", 0) >= 1000
        assert bad or slow, f"{s['path']} 既不报错也不慢，放在故障组里没有意义"


def test_限频那条带得走重试头():
    s = next(x for x in builtin_specs() if x["path"] == "/429/v1/chat/completions")
    headers = {k.lower(): v for k, v in (s.get("response_headers") or {}).items()}
    assert s["status_code"] == 429
    assert "retry-after" in headers or "retry-after-ms" in headers


def test_模型清单是GET():
    s = next(x for x in builtin_specs() if x["path"] == "/v1/models")
    assert s["method"].upper() == "GET"


def test_五个分组都有货():
    got = {s["category"] for s in builtin_specs()}
    assert got == set(CATEGORY_LABELS), f"缺了分组：{set(CATEGORY_LABELS) - got}"


def test_流式分片不是逐字符():
    """库默认一个字符一片。那会把一句话切成几十片，既不像真上游，
    也让「收到几片」这种被验证的指标失真。"""
    assert _BEHAVIOUR_DEFAULTS["sse_chunk_size"] > 1
